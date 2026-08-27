/**
 * Sayfalar hiçbir dış hosta bağlı olmadan açılmalı.
 *
 * Panellerin `<head>` bölümünde `https://cdn.socket.io/...` scripti,
 * `style.css` başında ise Google Fonts'a bir `@import` vardı. İkisi de
 * render-blocking: script HTML ayrıştırmayı durdurur, `@import` CSSOM'u
 * tamamlanmadan ilk boyamayı engeller.
 *
 * Makine tamamen çevrimdışıyken Windows anında `ERR_NAME_NOT_RESOLVED` döner ve
 * sorun görünmez. Modeme bağlı ama internet yokken DNS sorgusu router'a gidip
 * cevapsız kalır ve zaman aşımı beklenir — localhost 1 saniye yerine 10-15
 * saniyede açılıyordu. Ayrıca CDN erişilemediğinde `io` tanımsız kalıp canlı
 * bildirimler tamamen ölüyordu; bu yalnızca bir yavaşlık değil, işlev kaybıydı.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repositoryRoot = path.resolve(__dirname, '..', '..');

function read(relativePath) {
    return fs.readFileSync(path.join(repositoryRoot, relativePath), 'utf8');
}

const templateNames = fs
    .readdirSync(path.join(repositoryRoot, 'templates'))
    .filter(name => name.endsWith('.html'));

const styleSource = read('static/css/style.css');

// Bu sözleşme sayfa AÇILIŞ yolunu korur. Kasa'nın QR görseli de artık dış
// servisten gelmiyor; onun kendi testi tests/frontend/kasa_qr_local.test.cjs.
const PAGE_LOAD_HOST_PATTERN = /(?:src|href)\s*=\s*["']https?:\/\/[^"']+["']/gi;

test('no template pulls a page-load asset from an external host', () => {
    templateNames.forEach(name => {
        const source = read(path.join('templates', name));
        const matches = source.match(PAGE_LOAD_HOST_PATTERN) || [];
        assert.deepEqual(
            matches,
            [],
            `${name} açılışta dış bir hosta bağlanıyor: ${matches.join(', ')}`
        );
    });
});

test('every template that needs socket.io loads it locally', () => {
    const socketTemplates = templateNames.filter(name =>
        read(path.join('templates', name)).includes('socket.io')
    );

    assert.ok(
        socketTemplates.length > 0,
        'socket.io kullanan hiçbir sayfa bulunamadı; test yanlış yeri arıyor olmalı'
    );

    socketTemplates.forEach(name => {
        const source = read(path.join('templates', name));
        assert.match(
            source,
            /src="\/static\/js\/vendor\/socket\.io\.min\.js/,
            `${name} socket.io'yu yerelden yüklemeli`
        );
        assert.ok(
            !source.includes('cdn.socket.io'),
            `${name} hâlâ CDN'e bağlı`
        );
    });
});

test('the bundled socket.io client actually exists and matches the server', () => {
    const bundled = read('static/js/vendor/socket.io.min.js');

    assert.ok(bundled.length > 10000, 'yerel socket.io dosyası boş veya bozuk');
    assert.match(
        bundled,
        /Socket\.IO v4\.7\.5/,
        'sürüm CDN sürümüyle aynı olmalı, aksi halde sessiz uyumsuzluk riski var'
    );
});

test('the stylesheet has no remote @import', () => {
    assert.ok(
        !/@import\s+url\(\s*['"]?https?:/i.test(styleSource),
        'uzak @import ilk boyamayı bloklar; font yerelden gelmeli'
    );
});

test('the fonts are declared locally and the files are on disk', () => {
    const declared = [...styleSource.matchAll(/url\('(\/static\/fonts\/[^']+)'\)/g)]
        .map(match => match[1]);

    assert.ok(declared.length >= 2, 'yerel @font-face tanımı bulunamadı');

    declared.forEach(url => {
        const filePath = path.join(repositoryRoot, url.replace(/^\//, ''));
        assert.ok(
            fs.existsSync(filePath),
            `${url} bildirilmiş ama dosya yok — sayfa fontsuz açılır`
        );
        assert.ok(
            fs.statSync(filePath).size > 1000,
            `${url} boş görünüyor`
        );
    });
});

test('the Turkish latin-ext subset is present for both families', () => {
    // ğ (U+011F) ve ş (U+015F) latin-ext içinde. Yalnızca `latin` alt kümesi
    // indirilirse Türkçe metin yedek fontla karışık render edilir.
    ['outfit', 'plus-jakarta-sans'].forEach(family => {
        assert.ok(
            fs.existsSync(
                path.join(repositoryRoot, 'static/fonts', `${family}-latin-ext.woff2`)
            ),
            `${family} için latin-ext alt kümesi eksik`
        );
    });
});
