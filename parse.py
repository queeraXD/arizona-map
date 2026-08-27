import argparse
import concurrent.futures
import json
import sqlite3
import urllib.request
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent
PROPERTIES_DIR = OUTPUT_DIR / 'properties'
PROPERTIES_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DB = OUTPUT_DIR / 'cadastre.db'
SERVERS_JSON = OUTPUT_DIR / 'servers.json'
DEFAULT_SERVER_ID = 33
SERVER_IDS = list(range(1, 34))

SERVER_NAMES = {
    1: 'Phoenix', 2: 'Tucson', 3: 'Scottdale', 4: 'Chandler', 5: 'Brainburg',
    6: 'Saint-Rose', 7: 'Mesa', 8: 'Red-Rock', 9: 'Yuma', 10: 'Surprise',
    11: 'Prescott', 12: 'Glendale', 13: 'Kingman', 14: 'Winslow', 15: 'Payson',
    16: 'Gilbert', 17: 'Show Low', 18: 'Casa-Grande', 19: 'Page', 20: 'Sun-City',
    21: 'Queen-Creek', 22: 'Sedona', 23: 'Holiday', 24: 'Wednesday', 25: 'Yava',
    26: 'Faraway', 27: 'Bumble Bee', 28: 'Christmas', 29: 'Mirage', 30: 'Love',
    31: 'Drake', 32: 'Space', 33: 'Home',
}

BUSINESS_KEYWORDS = ['biz', 'business', 'shop', 'магаз', 'бизнес', 'biznes']
HOUSE_KEYWORDS = ['house', 'home', 'dom', 'дом', 'жил']

def classify_branch(key_name):
    k = str(key_name).lower()
    if any(s in k for s in BUSINESS_KEYWORDS):
        return 'business'
    if any(s in k for s in HOUSE_KEYWORDS):
        return 'house'
    return None

def extract_server(server_id, timeout=12):
    url = f'https://n-api.arizona-rp.com/api/map/{server_id}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Safari/537.36'
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw_data = json.loads(response.read().decode('utf-8'))

    extracted_items = []
    missing_id_counter = 0

    def find_objects(node, current_type=None):
        nonlocal missing_id_counter
        if isinstance(node, dict):
            has_x = any(k in node for k in ('x', 'lx'))
            has_y = any(k in node for k in ('y', 'ly'))
            if has_x and has_y:
                x = float(node.get('lx') if node.get('lx') is not None else node.get('x') or 0.0)
                y = float(node.get('ly') if node.get('ly') is not None else node.get('y') or 0.0)

                raw_id = None
                for key in ('id', 'house_id', 'biz_id', 'number'):
                    if node.get(key) is not None:
                        raw_id = node.get(key)
                        break

                if raw_id is None:
                    missing_id_counter += 1
                    obj_id = f'missing_id_{missing_id_counter}'
                else:
                    try:
                        obj_id = int(raw_id) - 1
                    except (ValueError, TypeError):
                        obj_id = raw_id

                raw_owner = str(node.get('owner', '')).strip()
                has_auction = node.get('hasAuction') == 1 or node.get('auction') is True
                owner_lower = raw_owner.lower().replace('_', ' ').strip()
                if not raw_owner or owner_lower in {
                    'none', 'null', '0', 'false', '', 'государство', 'the state', 'state', 'government'
                }:
                    owner = 'Государство'
                    status = 'Свободно'
                else:
                    owner = raw_owner
                    status = 'Занято'
                if has_auction:
                    status = 'Аукцион'

                obj_type = current_type or 'house'
                extracted_items.append({
                    'id': obj_id,
                    'server_id': server_id,
                    'type': obj_type,
                    'name': node.get('name') or ('Дом' if obj_type == 'house' else 'Бизнес'),
                    'owner': owner,
                    'status': status,
                    'x': x,
                    'y': y,
                    'price': node.get('auStartPrice') or node.get('price') or 0,
                    'district': node.get('district', 'Сан-Андреас'),
                })

            for k, v in node.items():
                branch_type = classify_branch(k) or current_type
                find_objects(v, branch_type)
        elif isinstance(node, list):
            for item in node:
                find_objects(item, current_type)

    find_objects(raw_data)
    return extracted_items, missing_id_counter

def write_server_json(server_id, items):
    filename = PROPERTIES_DIR / f'properties_public_{server_id}.json'
    filename.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    return filename

def update_db(all_results):
    conn = sqlite3.connect(OUTPUT_DB)
    try:
        cursor = conn.cursor()
        cursor.execute('DROP TABLE IF EXISTS properties')
        cursor.execute('''CREATE TABLE properties (
            id TEXT, server_id INTEGER, type TEXT, name TEXT,
            owner TEXT, status TEXT, x REAL, y REAL, price INTEGER, district TEXT
        )''')
        rows = []
        for server_id, items in all_results.items():
            rows.extend([
                (str(obj['id']), server_id, obj['type'], obj['name'], obj['owner'],
                 obj['status'], obj['x'], obj['y'], obj['price'], obj['district'])
                for obj in items
            ])
        cursor.executemany('INSERT INTO properties VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        conn.commit()
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description='Парсер кадастров Arizona RP для всех 33 серверов')
    parser.add_argument('--server', type=int, choices=SERVER_IDS, help='Проверить только один сервер')
    parser.add_argument('--timeout', type=int, default=12, help='Таймаут одного запроса, секунд')
    parser.add_argument('--workers', type=int, default=6, help='Количество параллельных запросов')
    args = parser.parse_args()

    ids = [args.server] if args.server else SERVER_IDS
    servers_manifest = [
        {'id': sid, 'name': SERVER_NAMES[sid], 'file': f'properties/properties_public_{sid}.json', 'default': sid == DEFAULT_SERVER_ID}
        for sid in SERVER_IDS
    ]
    SERVERS_JSON.write_text(json.dumps(servers_manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'[*] Проверка кадастра Arizona RP: серверов {len(ids)}')
    results = {}

    def worker(sid):
        try:
            items, missing = extract_server(sid, args.timeout)
            return sid, items, missing, None
        except Exception as exc:
            return sid, [], 0, f'{type(exc).__name__}: {exc}'

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(ids)))) as pool:
        for sid, items, missing, error in sorted(pool.map(worker, ids), key=lambda x: x[0]):
            if error:
                print(f'[-] #{sid:02d} {SERVER_NAMES[sid]}: ОШИБКА — {error}')
                continue
            results[sid] = items
            houses = sum(1 for i in items if i['type'] == 'house')
            businesses = sum(1 for i in items if i['type'] == 'business')
            out = write_server_json(sid, items)
            print(f'[+] #{sid:02d} {SERVER_NAMES[sid]}: {len(items)} объектов (домов: {houses}, бизнесов: {businesses}) → {out.name}')
            if missing:
                print(f'    [!] без id: {missing}')

    if DEFAULT_SERVER_ID in results:
        (PROPERTIES_DIR / 'properties_public.json').write_text(
            json.dumps(results[DEFAULT_SERVER_ID], ensure_ascii=False, indent=2), encoding='utf-8'
        )

    update_db(results)
    print(f'[*] Успешно: {len(results)}/{len(ids)}')
    print(f'[*] Список серверов: {SERVERS_JSON.name}')
    print(f'[*] База: {OUTPUT_DB.name}')

if __name__ == '__main__':
    main()
