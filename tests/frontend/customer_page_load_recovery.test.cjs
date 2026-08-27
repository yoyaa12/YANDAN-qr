/**
 * Müşteri menüsünün sayfa açılışı.
 *
 * Sayfa, elinde 90 dakikalık geçerli bir oturum olsa bile URL'deki 30 saniyelik
 * QR kodunu yeniden doğrulatmaya çalışıyordu. Kod ±1 pencere toleransıyla
 * yalnızca 30-59 saniye yaşadığı için, masada oturan müşteri sayfayı
 * yenilediğinde "Erişim Reddedildi" duvarına çarpıyordu. Sipariş vermiş
 * olanlar bunu fark etmiyordu: sunucudaki cihaz baypası onları kurtarıyordu.
 *
 * Kritik nokta kararın NEREDE verildiği. `localStorage`'da bir string bulunması
 * hiçbir şey kanıtlamaz — oraya herkes istediğini yazabilir. Bu yüzden istemci
 * saklanan token'ı sunucuya doğrulatmak zorunda.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repositoryRoot = path.resolve(__dirname, '..', '..');
const appSource = fs.readFileSync(
    path.join(repositoryRoot, 'static/js/app.js'),
    'utf8'
);

function extractBlock(signature, terminator = '\n}') {
    const start = appSource.indexOf(signature);
    assert.ok(start !== -1, 'kaynakta bulunamadı: ' + signature);
    const end = appSource.indexOf(terminator, start);
    assert.ok(end !== -1, 'kapanış bulunamadı: ' + signature);
    return appSource.slice(start, end + terminator.length);
}

/**
 * `hasValidCustomerSession`'ı sahte `localStorage` ve sahte `fetch` ile
 * çalıştırır: "sunucuya soruyor mu, cevabına uyuyor mu" ölçülerek yanıtlanır.
 */
function loadSandbox({ stored = {}, respond } = {}) {
    const calls = [];
    const store = { ...stored };

    const sandbox = {
        calls,
        localStorage: {
            getItem: key => (key in store ? store[key] : null),
            setItem: (key, value) => { store[key] = String(value); },
            removeItem: key => { delete store[key]; }
        },
        fetch: async (url, options) => {
            calls.push({ url, headers: (options && options.headers) || {} });
            return respond(url, options);
        },
        console: { error() { }, warn() { } }
    };

    vm.createContext(sandbox);
    vm.runInContext(extractBlock('async function hasValidCustomerSession('), sandbox);
    sandbox.store = store;
    return sandbox;
}

function ok(body) {
    return { ok: true, status: 200, json: async () => body };
}

function unauthorized() {
    return { ok: false, status: 401, json: async () => ({ detail: 'gecersiz' }) };
}

// ---------------------------------------------------------------------------
// 1. Karar sunucunun
// ---------------------------------------------------------------------------

test('a stored token is validated against the server, not trusted outright', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'c'.repeat(64) },
        respond: () => ok({ valid: true, masa_id: 44 })
    });

    const result = await sandbox.hasValidCustomerSession(44);

    assert.equal(result, true);
    assert.equal(sandbox.calls.length, 1, 'sunucuya tam olarak bir kez sorulmalı');
    assert.equal(sandbox.calls[0].url, '/api/auth/musteri/oturum');
    assert.equal(
        sandbox.calls[0].headers.Authorization,
        'Bearer ' + 'c'.repeat(64),
        'saklanan token yetkilendirme başlığında gönderilmeli'
    );
});

test('a forged localStorage value does not open the page', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'elle-yazilmis-sacmalik' },
        respond: () => unauthorized()
    });

    assert.equal(await sandbox.hasValidCustomerSession(44), false);
});

test('a rejected token is discarded so it cannot cause confusing 401s later', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'olu-token' },
        respond: () => unauthorized()
    });

    await sandbox.hasValidCustomerSession(44);

    assert.equal(sandbox.store['qr_session_token_44'], undefined);
});

test('no stored token means no request at all', async () => {
    const sandbox = loadSandbox({
        stored: {},
        respond: () => { throw new Error('istek yapılmamalıydı'); }
    });

    assert.equal(await sandbox.hasValidCustomerSession(44), false);
    assert.equal(sandbox.calls.length, 0);
});

// ---------------------------------------------------------------------------
// 2. Oturum yanlış masaya aitse geçmemeli
// ---------------------------------------------------------------------------

test('a session belonging to another table is refused', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'd'.repeat(64) },
        respond: () => ok({ valid: true, masa_id: 5 })
    });

    assert.equal(
        await sandbox.hasValidCustomerSession(44),
        false,
        'sunucu masaya göre kısıtlıyor; istemci de bunu teyit etmeli'
    );
});

// ---------------------------------------------------------------------------
// 3. Başarısızlık yönü güvenli taraf olmalı
// ---------------------------------------------------------------------------

test('a network failure falls back to the QR code instead of letting the page in', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'e'.repeat(64) },
        respond: () => { throw new TypeError('Failed to fetch'); }
    });

    assert.equal(await sandbox.hasValidCustomerSession(44), false);
    assert.equal(
        sandbox.store['qr_session_token_44'],
        'e'.repeat(64),
        'ağ hatası token`ı silmemeli; oturum hâlâ geçerli olabilir'
    );
});

test('a malformed response does not count as a valid session', async () => {
    const sandbox = loadSandbox({
        stored: { 'qr_session_token_44': 'f'.repeat(64) },
        respond: () => ({ ok: true, status: 200, json: async () => { throw new Error('bozuk'); } })
    });

    assert.equal(await sandbox.hasValidCustomerSession(44), false);
});

// ---------------------------------------------------------------------------
// 4. Sıra: önce oturum, sonra QR
// ---------------------------------------------------------------------------

test('the session check runs before the QR verification on page load', () => {
    const loadHandler = appSource.slice(
        appSource.indexOf("document.addEventListener('DOMContentLoaded'")
    );

    const sessionCheck = loadHandler.indexOf('hasValidCustomerSession');
    const qrVerify = loadHandler.indexOf('/verify-qr');
    const securityError = loadHandler.indexOf('showSecurityError');

    assert.ok(sessionCheck !== -1, 'sayfa açılışı saklanan oturuma bakmalı');
    assert.ok(qrVerify !== -1, 'QR doğrulama yolu korunmalı');
    assert.ok(
        sessionCheck < securityError && sessionCheck < qrVerify,
        'oturum kontrolü QR kontrolünden ve hata ekranından ÖNCE gelmeli; ' +
        'aksi halde geçerli oturumu olan müşteri yine kapıda kalır'
    );
});

test('a customer with a live session is not blocked for lacking a URL token', () => {
    const loadHandler = appSource.slice(
        appSource.indexOf("document.addEventListener('DOMContentLoaded'")
    );

    // "token yok" hata dalı, oturum geçersizken çalışan bloğun içinde olmalı.
    assert.match(
        loadHandler,
        /if \(state\.masaId !== 99 && !oturumGecerli\) \{\s*\n\s*if \(!tokenParam\) \{/,
        'URL`de kod olmaması, geçerli oturumu olan müşteriyi engellememeli'
    );
});
