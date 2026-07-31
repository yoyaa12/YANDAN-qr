import sys
sys.path.insert(0, r"c:\Users\emre\Desktop\QR odeme")
from app.database import execute_query

try:
    # Tablolar
    tables = execute_query("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' AND TABLE_CATALOG='RestoranQRDB' ORDER BY TABLE_NAME")
    print(f"=== TABLOLAR ({len(tables)} adet) ===")
    for t in tables:
        print(f"  - {t['TABLE_NAME']}")
    print()

    # Her tablo icin sutunlar
    for t in tables:
        tname = t['TABLE_NAME']
        cols = execute_query("""
            SELECT 
                c.COLUMN_NAME, c.DATA_TYPE, c.CHARACTER_MAXIMUM_LENGTH,
                c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE, c.COLUMN_DEFAULT,
                CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN 'YES' ELSE 'NO' END AS IS_PK,
                COLUMNPROPERTY(OBJECT_ID(c.TABLE_SCHEMA + '.' + c.TABLE_NAME), c.COLUMN_NAME, 'IsIdentity') AS IS_IDENTITY
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN (
                SELECT ku.TABLE_NAME, ku.COLUMN_NAME
                FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
                JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ) pk ON c.TABLE_NAME = pk.TABLE_NAME AND c.COLUMN_NAME = pk.COLUMN_NAME
            WHERE c.TABLE_NAME = ?
            ORDER BY c.ORDINAL_POSITION
        """, (tname,))
        print(f"=== {tname} ({len(cols)} sutun) ===")
        for col in cols:
            dt = col['DATA_TYPE'].upper()
            cml = col['CHARACTER_MAXIMUM_LENGTH']
            if cml and cml > 0:
                dt += f"({cml})"
            elif cml and cml == -1:
                dt += "(MAX)"
            elif col['NUMERIC_PRECISION'] and col['DATA_TYPE'].lower() == 'decimal':
                dt += f"({col['NUMERIC_PRECISION']},{col['NUMERIC_SCALE']})"
            flags = []
            if col['IS_PK'] == 'YES': flags.append("PK")
            if col['IS_IDENTITY'] == 1: flags.append("IDENTITY")
            if col['IS_NULLABLE'] == 'NO': flags.append("NOT NULL")
            if col['COLUMN_DEFAULT']: flags.append(f"DEFAULT={col['COLUMN_DEFAULT']}")
            fs = " | ".join(flags)
            print(f"  {col['COLUMN_NAME']:<35} {dt:<25} {fs}")
        print()

    # FK iliskileri
    fks = execute_query("""
        SELECT fk.name AS FK_Name, tp.name AS ParentTable, cp.name AS ParentColumn,
               tr.name AS ReferencedTable, cr.name AS ReferencedColumn
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables tp ON fkc.parent_object_id = tp.object_id
        JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
        JOIN sys.tables tr ON fkc.referenced_object_id = tr.object_id
        JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
        ORDER BY tp.name, fk.name
    """)
    print(f"=== FOREIGN KEY ({len(fks)} adet) ===")
    for fk in fks:
        print(f"  {fk['ParentTable']}.{fk['ParentColumn']} -> {fk['ReferencedTable']}.{fk['ReferencedColumn']}  ({fk['FK_Name']})")
    print()

    # Indexler
    idxs = execute_query("""
        SELECT t.name AS TableName, i.name AS IndexName, i.type_desc AS IndexType,
               i.is_unique, STRING_AGG(c.name, ', ') WITHIN GROUP (ORDER BY ic.key_ordinal) AS Cols
        FROM sys.indexes i
        JOIN sys.tables t ON i.object_id = t.object_id
        JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
        JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
        WHERE i.name IS NOT NULL
        GROUP BY t.name, i.name, i.type_desc, i.is_unique
        ORDER BY t.name, i.name
    """)
    print(f"=== INDEXLER ({len(idxs)} adet) ===")
    for idx in idxs:
        u = "UNIQUE " if idx['is_unique'] else ""
        print(f"  {idx['TableName']}: {u}{idx['IndexType']} [{idx['IndexName']}] -> ({idx['Cols']})")

    print("\n=== BITTI ===")
except Exception as e:
    print(f"HATA: {e}")
    import traceback
    traceback.print_exc()
