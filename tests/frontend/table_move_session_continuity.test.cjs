/**
 * Masa taşıma sonrası müşteri oturumunun sürekliliği ve tamamı ödenmiş
 * adisyonun kasada görünürlüğü.
 *
 * 1. Oturum token'ı `qr_session_token_<masaId>` altında tutuluyor. Masa
 *    taşındığında `state.masaId` değişiyor ama anahtar geride kalıyordu:
 *    `checkActiveOrder` hedef masanın anahtarını okuyup boş buluyor,
 *    Authorization başlığı gönderilmiyor ve istek 401 alıyordu. Müşteri de
 *    QR'ı yeniden okutmak zorunda kalıyor, açılan YENİ oturum satırı yüzünden
 *    taşınmış siparişlerini "benim siparişim" olarak göremiyordu.
 *
 * 2. Kasa, masanın bütün siparişleri ödenmişse adisyonu "sipariş bulunmuyor"
 *    diye boşaltıyordu. Ödenmiş olmak adisyonun kapandığı anlamına gelmez;
 *    masa hâlâ `dolu` ve fiş basılmayı bekliyor olabilir. Boşaltılan
 *    `currentTableItems` yüzünden F8 fişi de boş çıkıyordu.
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
const kasaSource = read('static/js/kasa.js');

/** `localStorage` API'sinin test edilebilir en küçük taklidi. */
function fakeStorage(initial = {}) {
    const data = { ...initial };
    return {
        data,
        getItem(key) {
            return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
        },
        setItem(key, value) {
            data[key] = String(value);
        },
        removeItem(key) {
            delete data[key];
        }
    };
}

/** `migrateSessionTokenToMasa`'yı sahte bir `localStorage` ile çalıştırır. */
function migrationSandbox(initial) {
    const start = appSource.indexOf('function migrateSessionTokenToMasa(');
    assert.ok(start !== -1, 'taşıma yardımcısı kaynakta bulunamadı');
    const end = appSource.indexOf('\n}', start);
    assert.ok(end !== -1, 'taşıma yardımcısının kapanışı bulunamadı');

    const storage = fakeStorage(initial);
    const sandbox = { localStorage: storage, console: { error() { } } };
    vm.createContext(sandbox);
    vm.runInContext(appSource.slice(start, end + 2), sandbox);
    return { storage, migrate: sandbox.migrateSessionTokenToMasa };
}

// ---------------------------------------------------------------------------
// 1. Oturum token'ı masa ile birlikte taşınıyor
// ---------------------------------------------------------------------------

test('the session token follows the guest to the new table', () => {
    const { storage, migrate } = migrationSandbox({ qr_session_token_5: 'token-abc' });

    migrate(5, 6);

    assert.equal(storage.getItem('qr_session_token_6'), 'token-abc');
});

test('the stale key is dropped so the old table cannot be addressed with it', () => {
    const { storage, migrate } = migrationSandbox({ qr_session_token_5: 'token-abc' });

    migrate(5, 6);

    assert.equal(
        storage.getItem('qr_session_token_5'),
        null,
        'oturum artık kaynak masaya ait değil; bırakılırsa orada 403 üretir'
    );
});

test('an existing session on the target table is not overwritten', () => {
    // Cihaz hedef masada zaten kendi oturumunu açmışsa geçerli olan odur.
    const { storage, migrate } = migrationSandbox({
        qr_session_token_5: 'token-eski',
        qr_session_token_6: 'token-hedef'
    });

    migrate(5, 6);

    assert.equal(storage.getItem('qr_session_token_6'), 'token-hedef');
    assert.equal(storage.getItem('qr_session_token_5'), null);
});

test('a second call is harmless', () => {
    // `masa_tasindi` socket olayı ve 3 saniyelik yoklama aynı taşımayı iki kez
    // bildirebiliyor; ikinci çağrı taşınmış token'ı silmemeli.
    const { storage, migrate } = migrationSandbox({ qr_session_token_5: 'token-abc' });

    migrate(5, 6);
    migrate(5, 6);

    assert.equal(storage.getItem('qr_session_token_6'), 'token-abc');
});

test('a device with no session is left alone', () => {
    const { storage, migrate } = migrationSandbox({ qr_device_id: 'dev-1' });

    migrate(5, 6);

    assert.equal(storage.getItem('qr_session_token_6'), null);
    assert.equal(storage.getItem('qr_device_id'), 'dev-1');
});

test('a move onto the same table changes nothing', () => {
    const { storage, migrate } = migrationSandbox({ qr_session_token_5: 'token-abc' });

    migrate(5, 5);

    assert.equal(storage.getItem('qr_session_token_5'), 'token-abc');
});

test('the migration runs before the state switches to the new table', () => {
    // Sıra ters olsaydı `fromMasaId` ile okunan anahtar doğru olsa bile
    // taşımanın etkisi bir sonraki `checkActiveOrder` turuna gecikirdi.
    const moveBody = appSource.slice(
        appSource.indexOf('window.handleTableMove ='),
        appSource.indexOf('// AKICI VE KESİNTİSİZ NATIVE KATEGORİ KAYDIRMA SİSTEMİ')
    );

    const migrateIndex = moveBody.indexOf('migrateSessionTokenToMasa(');
    const stateIndex = moveBody.indexOf('state.masaId = parseInt(toMasaId)');

    assert.ok(migrateIndex !== -1, 'taşıma çağrılmıyor');
    assert.ok(stateIndex !== -1, 'masa kimliği güncellenmiyor');
    assert.ok(migrateIndex < stateIndex, 'token, masa kimliği değişmeden taşınmalı');
});

// ---------------------------------------------------------------------------
// 2. Tamamı ödenmiş ama kapanmamış adisyon kasada görünür kalıyor
// ---------------------------------------------------------------------------

test('a fully paid but still open check is not emptied from the till screen', () => {
    const guard = kasaSource.slice(
        kasaSource.indexOf('const openMasaOrders = allMasaOrders.filter'),
        kasaSource.indexOf('const modeSelectorHtml =')
    );

    assert.ok(
        !/openMasaOrders\.length === 0[\s\S]{0,40}\{[\s\S]{0,200}currentTableItems = \[\]/.test(guard),
        'ödenmiş olmak adisyonu boşaltmamalı; masa hâlâ dolu ve fiş bekliyor olabilir'
    );
    assert.match(
        guard,
        /if \(allMasaOrders\.length === 0 \|\| table\.durum === 'bos'\)/,
        'adisyonun bittiği an yalnızca kapanmış siparişler veya boş masa ile anlaşılmalı'
    );
});

test('the receipt does not attribute till money to order-time payment', () => {
    const receiptBody = kasaSource.slice(
        kasaSource.indexOf('window.printReceiptPreview ='),
        kasaSource.indexOf('window.closeModal =')
    );

    assert.match(
        receiptBody,
        /const showBreakdown = paidAtOrderTime > 0\.005 && paidAtTill > 0\.005/,
        'kırılım yalnızca iki kalem de gerçekten varken yazılmalı'
    );

    const breakdownIndex = receiptBody.indexOf('Sipariş anında ödenen:');
    const guardIndex = receiptBody.indexOf('if (showBreakdown)');
    assert.ok(guardIndex !== -1 && guardIndex < breakdownIndex, 'kırılım korumasız basılıyor');
});
