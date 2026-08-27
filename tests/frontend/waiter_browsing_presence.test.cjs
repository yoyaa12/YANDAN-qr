/**
 * Garson panelinde "menü inceliyor" listesi tek masaya düşüyordu.
 *
 * Kusur `renderWaiterDashboard` sonundaki `activeBrowsingTables = {}` satırıydı:
 * render her socket olayında çalıştığı için harita sürekli boşaltılıyor, ekranda
 * yalnızca en son gelen olayın masası kalıyordu. Gizli sekme/çerez ile ilgisi
 * yoktu; DB'de oturumlar duruyor, panel gösteremiyordu.
 *
 * İkinci ve bağımsız kusur: sunucu bu bilgiyi `BROWSING_TABLES` içinde tutup
 * `/api/masalar` yanıtında `secim_durumu` olarak dönüyordu, ama hiçbir panel bu
 * alanı okumuyordu. Bu yüzden panel her F5'te boş başlıyordu.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repositoryRoot = path.resolve(__dirname, '..', '..');
const waiterSource = fs.readFileSync(
    path.join(repositoryRoot, 'static/js/waiter.js'),
    'utf8'
);

function extractBlock(signature, terminator = '\n}') {
    const start = waiterSource.indexOf(signature);
    assert.ok(start !== -1, 'kaynakta bulunamadı: ' + signature);
    const end = waiterSource.indexOf(terminator, start);
    assert.ok(end !== -1, 'kapanış bulunamadı: ' + signature);
    return waiterSource.slice(start, end + terminator.length);
}

/**
 * Presence mantığını sahte DOM ve sahte `authFetch` ile gerçekten çalıştırır:
 * "render sonrası harita ne halde" sorusu kaynak taramasıyla değil, ölçülerek
 * yanıtlanır.
 */
function presenceSandbox({ browsing = {}, orders = [], masalar = [] } = {}) {
    let renderedHtml = '';

    const sandbox = {
        activeBrowsingTables: { ...browsing },
        allRawOrders: orders,
        waiterOrders: orders,
        tables: [],
        activeDetailMasaId: null,
        BROWSING_ENTRY_TTL_MS: 30 * 60 * 1000,
        escapeHtml: value => String(value),
        authFetch: async () => ({ ok: true, json: async () => masalar }),
        console: { warn() { }, error() { } },
        document: {
            getElementById(id) {
                if (id !== 'waiterDashboardGrid') return null;
                return {
                    set innerHTML(value) { renderedHtml = value; },
                    get innerHTML() { return renderedHtml; }
                };
            }
        }
    };

    vm.createContext(sandbox);
    vm.runInContext(
        [
            extractBlock('function toPositiveInteger('),
            extractBlock('function getFormattedMasaNo('),
            extractBlock('function pruneBrowsingTables('),
            extractBlock('function renderWaiterDashboard('),
            extractBlock('async function syncBrowsingTablesFromServer(')
        ].join('\n\n'),
        sandbox
    );

    return { sandbox, html: () => renderedHtml };
}

const now = Date.now();

function browsingEntry(masaNo, overrides = {}) {
    return { masa_no: masaNo, time: now, item_count: 0, last_item: '', ...overrides };
}

// ---------------------------------------------------------------------------
// 1. Asıl kusur: render haritayı boşaltmamalı
// ---------------------------------------------------------------------------

test('rendering does not wipe the browsing map', () => {
    const { sandbox } = presenceSandbox({
        browsing: { 5: browsingEntry('S-1'), 6: browsingEntry('S-2') }
    });

    sandbox.renderWaiterDashboard();

    assert.deepEqual(
        Object.keys(sandbox.activeBrowsingTables).sort(),
        ['5', '6'],
        'render sonrası masalar haritada kalmalı; boşaltmak sonraki renderı tek masaya düşürür'
    );
});

test('every browsing table stays on screen across repeated renders', () => {
    const { sandbox, html } = presenceSandbox({
        browsing: {
            5: browsingEntry('S-1'),
            6: browsingEntry('S-2'),
            7: browsingEntry('S-3')
        }
    });

    // Alakasız bir olay (ör. başka masada durum değişikliği) render tetikler.
    sandbox.renderWaiterDashboard();
    sandbox.renderWaiterDashboard();

    const markup = html();
    ['S-1', 'S-2', 'S-3'].forEach(masaNo => {
        assert.ok(
            markup.includes(masaNo),
            masaNo + ' ikinci renderdan sonra da görünmeli'
        );
    });
});

// ---------------------------------------------------------------------------
// 2. Yaşlanmış kayıtlar yine de düşmeli
// ---------------------------------------------------------------------------

test('a stale browsing entry is dropped instead of lingering forever', () => {
    const { sandbox } = presenceSandbox({
        browsing: {
            5: browsingEntry('S-1'),
            6: browsingEntry('S-2', { time: now - (31 * 60 * 1000) })
        }
    });

    sandbox.renderWaiterDashboard();

    assert.deepEqual(
        Object.keys(sandbox.activeBrowsingTables),
        ['5'],
        'TTLi aşan kayıt düşmeli, taze olan kalmalı'
    );
});

test('an entry without a usable timestamp is dropped', () => {
    const { sandbox } = presenceSandbox({
        browsing: { 5: { masa_no: 'S-1', item_count: 0, last_item: '' } }
    });

    sandbox.renderWaiterDashboard();

    assert.deepEqual(Object.keys(sandbox.activeBrowsingTables), []);
});

// ---------------------------------------------------------------------------
// 3. Sunucu anlık görüntüsü okunmalı (F5 sonrası liste boş kalmamalı)
// ---------------------------------------------------------------------------

test('the server snapshot repopulates the map after a reload', async () => {
    const { sandbox } = presenceSandbox({
        browsing: {},
        masalar: [
            {
                id: 5,
                masa_no: 'S-1',
                durum: 'bos',
                secim_durumu: { masa_no: 'S-1', item_count: 2, last_item: 'Kunefe' }
            },
            {
                id: 6,
                masa_no: 'S-2',
                durum: 'bos',
                secim_durumu: { masa_no: 'S-2', item_count: 0, last_item: '' }
            },
            { id: 7, masa_no: 'S-3', durum: 'bos', secim_durumu: null }
        ]
    });

    await sandbox.syncBrowsingTablesFromServer();

    assert.deepEqual(
        Object.keys(sandbox.activeBrowsingTables).sort(),
        ['5', '6'],
        'secim_durumu taşıyan her masa haritaya girmeli'
    );
    assert.equal(sandbox.activeBrowsingTables[5].item_count, 2);
    assert.equal(sandbox.activeBrowsingTables[5].last_item, 'Kunefe');
    assert.equal(
        sandbox.tables.length,
        3,
        'aynı çağrı masa listesini de tazelemeli'
    );
});

test('a table the server no longer reports is removed', async () => {
    const { sandbox } = presenceSandbox({
        browsing: {
            5: browsingEntry('S-1'),
            9: browsingEntry('S-5', { time: now - 60000 })
        },
        masalar: [
            {
                id: 5,
                masa_no: 'S-1',
                durum: 'bos',
                secim_durumu: { masa_no: 'S-1', item_count: 0, last_item: '' }
            },
            { id: 9, masa_no: 'S-5', durum: 'bos', secim_durumu: null }
        ]
    });

    await sandbox.syncBrowsingTablesFromServer();

    assert.deepEqual(
        Object.keys(sandbox.activeBrowsingTables),
        ['5'],
        'sunucu artık saymıyorsa masa panelde kalmamalı'
    );
});

test('a socket event that arrived during the request survives the sync', async () => {
    const { sandbox } = presenceSandbox({ browsing: {}, masalar: [] });

    // İstek uçarken gelen canlı olayı taklit eder: anlık görüntüden tazedir.
    const pending = sandbox.syncBrowsingTablesFromServer();
    sandbox.activeBrowsingTables[12] = browsingEntry('S-8', { time: Date.now() + 5000 });
    await pending;

    assert.deepEqual(
        Object.keys(sandbox.activeBrowsingTables),
        ['12'],
        'anlık görüntüden yeni olan kayıt silinmemeli'
    );
});

// ---------------------------------------------------------------------------
// 4. Kusurun kendisi geri gelmesin
// ---------------------------------------------------------------------------

test('the browsing map is never reassigned to an empty object after a render', () => {
    const renderBody = extractBlock('function renderWaiterDashboard(');
    assert.ok(
        !/activeBrowsingTables\s*=\s*\{\s*\}/.test(renderBody),
        'render haritayı topluca sıfırlarsa panel yine tek masaya düşer'
    );
});
