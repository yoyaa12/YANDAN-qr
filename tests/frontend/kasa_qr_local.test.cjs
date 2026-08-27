/**
 * Kasa ekranındaki QR kodu.
 *
 * İki ayrı kusur vardı ve ikisi de aynı fonksiyondaydı:
 *
 * 1. QR görseli `api.qrserver.com`'dan çekiliyordu. Tarayıcıda ölçülen bedel:
 *    ilk açılışta 647 ms (DNS + TLS), sonraki üretimlerde ~100 ms. Token 30
 *    saniyede bir yenilendiği için modal açık kaldığı sürece bu bedel tekrar
 *    tekrar ödeniyordu; internet yokken QR karesi hiç gelmiyordu.
 *
 * 2. Panel localhost'tan açıldığında hedef adres sabit kodlanmış
 *    `http://192.168.1.100:8000` idi. Makinenin IP'si DHCP ile değiştiği için
 *    QR sessizce ölü bir adres taşıyordu: telefonla okutulunca hiçbir şey
 *    açılmıyor, sebebi de hiçbir yerde görünmüyordu.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repositoryRoot = path.resolve(__dirname, '..', '..');

function read(relativePath) {
    return fs.readFileSync(path.join(repositoryRoot, relativePath), 'utf8');
}

const kasaSource = read('static/js/kasa.js');
const kasaTemplate = read('templates/kasa.html');
const qrcode = require(path.join(repositoryRoot, 'static/js/vendor/qrcode-generator.js'));

function extractBlock(signature, terminator = '\n}') {
    const start = kasaSource.indexOf(signature);
    assert.ok(start !== -1, 'kaynakta bulunamadı: ' + signature);
    const end = kasaSource.indexOf(terminator, start);
    assert.ok(end !== -1, 'kapanış bulunamadı: ' + signature);
    return kasaSource.slice(start, end + terminator.length);
}

/** QR yardımcılarını sahte bir `window` ve DOM düğümü ile çalıştırır. */
function qrSandbox(origin) {
    const sandbox = {
        qrcode,
        console: { error() { } },
        window: { location: { origin, host: origin.replace(/^https?:\/\//, '') } }
    };
    sandbox.window.window = sandbox.window;
    vm.createContext(sandbox);
    vm.runInContext(
        [
            extractBlock('function buildMasaQrTarget('),
            extractBlock('function renderLocalQrCode(')
        ].join('\n\n'),
        sandbox
    );
    return sandbox;
}

/** `innerHTML` yazılan bir kabı taklit eder; SVG'ye özellik atanmasını yutar. */
function fakeContainer() {
    return {
        innerHTML: '',
        textContent: '',
        querySelector() {
            return { setAttribute() { }, style: {} };
        }
    };
}

// ---------------------------------------------------------------------------
// 1. Vendor kopyası bozulmamış ve doğru QR üretiyor
// ---------------------------------------------------------------------------

test('the vendored library reproduces the upstream reference output byte for byte', () => {
    // Kütüphanenin kendi test fixture'ı. Dosya bozulmuş veya elle
    // değiştirilmiş olsaydı bu eşitlik bozulurdu.
    const sourceText = 'http://www.example.com/ążśźęćńół';
    const expectedWidth = 74;

    const qr = qrcode(-1, 'M');
    qr.addData(unescape(encodeURI(sourceText)));
    qr.make();

    assert.equal(qr.getModuleCount(), 29);
    assert.match(qr.createImgTag(), new RegExp(`width="${expectedWidth}"`));
    assert.match(qr.createImgTag(), /^<img src="data:image\/gif;base64,R0lGODdhSgBKAIAAAAAAAP\/\/\/ywAAAAASgBKAAAC/);
});

test('the encoded output actually depends on the data', () => {
    function matrix(text) {
        const qr = qrcode(0, 'M');
        qr.addData(text);
        qr.make();
        return qr.createASCII(1, 0);
    }

    const first = matrix('http://10.0.0.5:8000/m/44?token=111111');
    const same = matrix('http://10.0.0.5:8000/m/44?token=111111');
    const different = matrix('http://10.0.0.5:8000/m/44?token=222222');

    assert.equal(first, same, 'aynı veri aynı QR üretmeli');
    assert.notEqual(
        first,
        different,
        'token değiştiğinde QR değişmeli; sabit bir kare veriyi taşımıyor demektir'
    );
});

// ---------------------------------------------------------------------------
// 2. Hedef adres: sabit IP yok, panelin adresi kullanılıyor
// ---------------------------------------------------------------------------

test('the QR target follows the panel origin instead of a hardcoded address', () => {
    const sandbox = qrSandbox('http://10.198.1.138:8000');
    const target = sandbox.buildMasaQrTarget('/m/44?token=907231');

    assert.equal(target.url, 'http://10.198.1.138:8000/m/44?token=907231');
    assert.equal(target.isLocalOnly, false);
});

test('no hardcoded LAN address survives anywhere in the panel code', () => {
    const executable = kasaSource
        .split('\n')
        .filter(line => !line.trim().startsWith('//') && !line.trim().startsWith('*'))
        .join('\n');

    assert.ok(
        !/\d+\.\d+\.\d+\.\d+:\d+/.test(executable),
        'sabit kodlanmış bir IP:port QR`ı sessizce ölü bir adrese yollar'
    );
});

test('a localhost origin is flagged so the operator is not left guessing', () => {
    ['http://localhost:8000', 'http://127.0.0.1:8000', 'http://[::1]:8000'].forEach(origin => {
        assert.equal(
            qrSandbox(origin).buildMasaQrTarget('/m/5?token=1').isLocalOnly,
            true,
            `${origin} yalnızca yerel olarak işaretlenmeli`
        );
    });
});

test('a lookalike hostname is not mistaken for localhost', () => {
    ['http://localhost-evil.com', 'http://127.0.0.1.evil.com', 'https://restoran.example.com'].forEach(origin => {
        assert.equal(
            qrSandbox(origin).buildMasaQrTarget('/m/5?token=1').isLocalOnly,
            false,
            `${origin} yerel sayılmamalı`
        );
    });
});

// ---------------------------------------------------------------------------
// 3. Çizim yerelde, dış servise gitmeden
// ---------------------------------------------------------------------------

test('the QR is drawn locally as inline SVG', () => {
    const sandbox = qrSandbox('http://10.0.0.5:8000');
    const container = fakeContainer();

    sandbox.renderLocalQrCode(container, 'http://10.0.0.5:8000/m/44?token=907231');

    assert.match(container.innerHTML, /^<svg/, 'satır içi SVG üretilmeli');

    // `xmlns="http://www.w3.org/2000/svg"` bir ad alanı bildirimidir, ağ isteği
    // değildir. Aranan şey gerçekten kaynak çeken bir öğe olup olmadığı.
    assert.ok(
        !/<image|xlink:href|src=|url\(/i.test(container.innerHTML),
        'çizilen QR hiçbir dış kaynak çekmemeli'
    );
    assert.ok(
        !/qrserver/i.test(container.innerHTML),
        'dış QR servisi çizime sızmamalı'
    );
});

test('a missing library degrades visibly instead of showing a blank square', () => {
    const sandbox = qrSandbox('http://10.0.0.5:8000');
    sandbox.qrcode = undefined;
    const container = fakeContainer();

    sandbox.renderLocalQrCode(container, 'http://10.0.0.5:8000/m/44?token=1');

    assert.match(container.textContent, /QR/);
    assert.equal(container.innerHTML, '');
});

test('the panel no longer calls the external QR service', () => {
    assert.ok(
        !/api\.qrserver\.com\/v1/.test(
            kasaSource.split('\n').filter(l => !l.trim().startsWith('//')).join('\n')
        ),
        'dış QR servisi her token yenilemesinde bir ağ gidiş-dönüşü demektir'
    );
});

// ---------------------------------------------------------------------------
// 4. Yükleme sırası
// ---------------------------------------------------------------------------

test('the QR library is served locally and loads before the panel script', () => {
    const libraryIndex = kasaTemplate.indexOf('/static/js/vendor/qrcode-generator.js');
    const panelIndex = kasaTemplate.indexOf('/static/js/kasa.js');

    assert.ok(libraryIndex !== -1, 'QR kütüphanesi kasa sayfasında yüklenmeli');
    assert.ok(
        libraryIndex < panelIndex,
        'kütüphane kasa.js`ten sonra yüklenirse ilk QR çizimi başarısız olur'
    );
    assert.ok(
        fs.existsSync(path.join(repositoryRoot, 'static/js/vendor/qrcode-generator.js')),
        'kütüphane dosyası diskte yok'
    );
});
