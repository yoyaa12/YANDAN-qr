/**
 * Ürün seçeneklerinin tek kaynağı veritabanıdır.
 *
 * Önceden fiyat farkları (pizza boyu, porsiyon çarpanı, ekstra malzeme) İKİ
 * ayrı yerde, iki ayrı dilde elle yazılıydı:
 *
 *     static/js/app.js                  PIZZA_SIZES / PORTION_OPTIONS / DESSERT_EXTRAS
 *     app/services/siparis_service.py   "Büyük Boy" in note -> += 85.0
 *
 * Aralarındaki tek bağ Türkçe bir metindi. Sunucu, güvendiği kaynakta
 * (katalog) opsiyon bulamadığı için fiyatı müşterinin serbest metninden
 * türetmek zorunda kalıyordu. Bu testler, o iki kopyanın geri gelmediğini ve
 * istemcinin artık FİYAT değil SEÇİM gönderdiğini sabitler.
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

const appSource = read('static/js/app.js');
const waiterSource = read('static/js/waiter.js');

/** Yorum satırlarını atar: bir kuralı yalnızca çalışan kod üzerinde ararız. */
function executable(source) {
    return source
        .split('\n')
        .filter(line => {
            const t = line.trim();
            return !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*');
        })
        .join('\n');
}

// ---------------------------------------------------------------------------
// 1. Sabit fiyat listeleri geri gelmedi
// ---------------------------------------------------------------------------

test('the option arrays no longer carry hardcoded prices', () => {
    const code = executable(appSource);

    assert.match(code, /let PIZZA_SIZES = \[\];/, 'boy listesi API`den dolmalı');
    assert.match(code, /let PORTION_OPTIONS = \[\];/, 'porsiyon listesi API`den dolmalı');
    assert.match(code, /let DESSERT_EXTRAS = \[\];/, 'ekstra listesi API`den dolmalı');

    assert.ok(
        !/priceDiff:\s*\d/.test(code),
        'fiyat farkı istemciye sabit yazılmamalı; katalogdan gelmeli'
    );
    assert.ok(
        !/multiplier:\s*\d/.test(code),
        'porsiyon çarpanı istemciye sabit yazılmamalı'
    );
});

test('the options are fetched from the catalogue endpoint', () => {
    assert.match(
        appSource,
        /fetch\('\/api\/urun-opsiyonlari'\)/,
        'seçenekler sunucudan okunmalı'
    );
});

test('the option loader survives a failed request', () => {
    // Seçenekler gelmezse ürün yine taban fiyatından sipariş edilebilmeli;
    // sessizce yanlış bir fiyat göstermektense seçenek bölümü hiç açılmamalı.
    const start = appSource.indexOf('async function loadMenuOptions()');
    assert.ok(start !== -1, 'yükleyici bulunamadı');
    const body = appSource.slice(start, appSource.indexOf('\n}', start));

    assert.match(body, /catch/, 'hata yakalanmalı');
    assert.match(body, /PIZZA_SIZES = \[\]/, 'hata durumunda liste boş bırakılmalı');
});

test('a section is not opened when its options failed to load', () => {
    assert.match(
        appSource,
        /isPizza && PIZZA_SIZES\.length > 0/,
        'boş seçenek listesiyle boyut bölümü açılmamalı'
    );
    assert.match(
        appSource,
        /isDish && PORTION_OPTIONS\.length > 0/,
        'boş seçenek listesiyle porsiyon bölümü açılmamalı'
    );
});

// ---------------------------------------------------------------------------
// 2. İstemci fiyat değil SEÇİM gönderiyor
// ---------------------------------------------------------------------------

test('the order payload carries the selected option ids', () => {
    const payloadBlock = appSource.slice(
        appSource.indexOf('urunler: state.cart.map'),
        appSource.indexOf('urunler: state.cart.map') + 400
    );

    assert.match(payloadBlock, /opsiyon_ids: item\.opsiyon_ids \|\| \[\]/);
});

test('every cart entry carries an option id list', () => {
    // Hızlı ekleme yolu seçenek sormaz ama alanı yine de taşımalı; aksi halde
    // sipariş gövdesinde `undefined` giderdi.
    const eklemeSayisi = (appSource.match(/opsiyon_ids: \[\]/g) || []).length;
    assert.ok(eklemeSayisi >= 1, 'seçeneksiz kalem boş liste taşımalı');
    assert.match(appSource, /opsiyon_ids: secilenOpsiyonIds/, 'seçilenler sepete yazılmalı');
});

test('the staff edit resends the option ids', () => {
    const saveBody = waiterSource.slice(
        waiterSource.indexOf('window.saveEditedOrder ='),
        waiterSource.length
    );

    assert.match(
        saveBody,
        /opsiyon_ids: i\.opsiyon_ids \|\| \[\]/,
        'düzenleme seçimi sıfırlarsa büyük boy pizza taban fiyatına düşer'
    );
});

// ---------------------------------------------------------------------------
// 3. Seçim gerçekten toplanıyor (davranış testi)
// ---------------------------------------------------------------------------

test('the option ids of size, portion and extras are collected', () => {
    // `confirmAddToCart` içindeki toplama bloğunu izole edip çalıştırırız:
    // kaynakta geçtiğini görmek yetmez, doğru kimlikleri toplaması gerekir.
    const sandbox = {
        state: {
            selectedSize: { name: 'Büyük Boy', priceDiff: 85, opsiyonId: 3 },
            selectedPortion: { name: '1.5 Porsiyon', multiplier: 1.4, opsiyonId: 6 },
            selectedExtras: [
                { name: 'Ekstra Manda Kaymağı', price: 35, opsiyonId: 8 },
                { name: 'Ekstra Antep Fıstığı Tozu', price: 30, opsiyonId: 11 }
            ],
            selectedFreeDrink: null
        },
        isPizza: true,
        isDish: false,
        calculatedUnitPrice: 200,
        fullTitle: 'Pizza',
        combinedNotes: [],
        secilenOpsiyonIds: []
    };
    vm.createContext(sandbox);

    const start = appSource.indexOf('    const secilenOpsiyonIds = [];');
    assert.ok(start !== -1, 'toplama bloğu bulunamadı');
    const end = appSource.indexOf('if (manualNote)', start);
    const block = appSource
        .slice(start, end)
        .replace('const secilenOpsiyonIds = [];', '');

    vm.runInContext(block, sandbox);

    assert.deepEqual(
        sandbox.secilenOpsiyonIds,
        [3, 8, 11],
        'pizza yolunda boy + ekstralar toplanmalı (porsiyon bu dalda uygulanmaz)'
    );
    assert.equal(sandbox.calculatedUnitPrice, 350, '200 + 85 + 35 + 30');
    assert.ok(
        sandbox.combinedNotes.includes('Büyük Boy'),
        'boyut nota da yazılmalı: ürün adı sunucuya gönderilmiyor, mutfak aksi halde göremez'
    );
});
