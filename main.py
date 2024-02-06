import aiohttp
import asyncio
import json
import db
import math
from threading import Thread

map_end = False

config = {
    "map_async_level": 5,
    "pass_async_level": 8,
    "pass_threads_cnt": 3,
    "scan_passwords": True,
    "save_raw_answers": False,
    "rescan_on_error": True,
    "limit_rescans": True
}

try:
    with open("config.json", "r") as f:
        config = json.load(f)
except:
    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)

async def load(session, tile1, tile2, zoom, rescan_level=0):
    if rescan_level > 8 and config["limit_rescans"]:
        print("Too many rescans")
        raise BaseException("Too many rescans")
    headers = { 
      'Content-type': 'application/json',  
      'Accept': 'text/plain', 
      'Host': '3wifi.stascorp.com' 
    } 
    r = await session.get(f'http://134.0.119.34/3wifi.php?a=map&scat=1&tileNumber={tile1},{tile2}&zoom={zoom}', headers=headers)
    to_parse = await r.text()
    stdata = to_parse.find("{\"error\":")
    if stdata == -1:
        print("Didn't find data. (read log.txt)")
        with open("log.txt", "w", encoding="utf-8") as f:
            f.write("Didn't find \"{\"error\":\" in data\ndata:\n" + to_parse)
        print("rescan")
        await asyncio.sleep(0.5)
        await load(session, tile1, tile2, zoom, rescan_level=rescan_level + 1)
        return
    to_parse = to_parse[stdata:-2]
    cur = db.db.cursor()
    if config["save_raw_answers"] and to_parse != "{\"error\":null,\"data\":{\"type\":\"FeatureCollection\",\"features\":[]}}":
        cur.execute("INSERT INTO raw_map (raw_answer) VALUES (?)", (to_parse, ))
        cur.execute("SELECT max(id) FROM raw_map")
        raw_id = cur.fetchone()[0]
    else:
        raw_id = -1
    try:
        to_parse = json.loads(to_parse)["data"]["features"]
    except Exception as e:
        print("JSON parse error. (read log.txt)")
        with open("log.txt", "w", encoding="utf-8") as f:
            f.write("JSON parse error\ndata:\n" + to_parse)
        print("rescan")
        await asyncio.sleep(0.5)
        await load(session, tile1, tile2, zoom, rescan_level=rescan_level + 1)
        return
    print(f"Found {len(to_parse)} networks")
    if len(to_parse) > 0:
        for point in to_parse:
            if point["type"] == "Feature" or point["type"] == "Cluster":
                properties = point.get("properties")
                if properties == None:
                    print("Bad cluster")
                    continue
                hintContent = properties.get("hintContent")
                if hintContent == None:
                    print("Bad cluster")
                    continue
                if len(hintContent) == 0:
                    print("Bad cluster")
                    continue
                hintContent = hintContent.split("<hr>")
                coords = point["geometry"]["coordinates"]
                for i in hintContent:
                    data = i.split("<br>")[0:2]
                    net = (
                        data[1].replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<"),
                        data[0],
                        coords[0],
                        coords[1],
                        raw_id
                    )
                    cur.execute("INSERT INTO networks (SSID, BSSID, lat, lon, rawmap_id) VALUES ((?),(?),(?),(?),(?))", net)
        try:
            db.db.commit()
        except:
            pass
    cur.close()
    

def from_geo_to_pixels(lat, long, projection, z):
    rho = math.pow(2, z + 8) / 2
    beta = lat * math.pi / 180
    phi = (1 - projection * math.sin(beta)) / (1 + projection * math.sin(beta))
    theta = math.tan(math.pi / 4 + beta / 2) * math.pow(phi, projection / 2)
    x_p = rho * (1 + long / 180)
    y_p = rho * (1 - math.log(theta) / math.pi)
    return [x_p // 256, y_p // 256]

async def main():
    pos1str = input("pos1: ")
    progress = 0
    if pos1str.startswith("save"):
        task_id = pos1str[4:]
        cur = db.db.cursor()
        cur.execute("SELECT * FROM tasks WHERE id=(?)", (task_id,))
        task_d = cur.fetchone()
        if task_d == None or len(task_d) < 1:
            print("Неверный task_id")
            return
        #tiles_to_scan = json.loads(task_d[1])
        min_maxTileX = json.loads(task_d[5])
        min_maxTileY = json.loads(task_d[6])
        tiles_cnt = (min_maxTileX[1] - min_maxTileX[0] + 1) * (min_maxTileY[1] - min_maxTileY[0] + 1)
        z = task_d[4]
        progress = task_d[2] + 1
    else:
        border1 = pos1str.split(",")
        border1[0] = float(border1[0])
        border1[1] = float(border1[1])
        border2 = input("pos2: ").split(",")
        border2[0] = float(border2[0])
        border2[1] = float(border2[1])
        z = input("z (enter - 17): ")
        if len(z.strip()) == 0:
            z = 17
        else:
            z = int(z)
        projection = 0.0818191908426
        pixel_coords1 = from_geo_to_pixels(border1[0], border1[1], projection, z)
        pixel_coords2 = from_geo_to_pixels(border2[0], border2[1], projection, z)
        min_maxTileX = [int(min(pixel_coords1[0], pixel_coords2[0])), int(max(pixel_coords1[0], pixel_coords2[0]))]
        min_maxTileY = [int(min(pixel_coords1[1], pixel_coords2[1])), int(max(pixel_coords1[1], pixel_coords2[1]))]
        tiles_cnt = (min_maxTileX[1] - min_maxTileX[0] + 1) * (min_maxTileY[1] - min_maxTileY[0] + 1)
        print(f"Need to scan {tiles_cnt} tiles")
        middle_coords = [round((border1[0] + border2[0]) * 500000) / 1000000, round((border1[1] + border2[1]) * 500000) / 1000000]
        cur = db.db.cursor()
        cur.execute("INSERT INTO tasks (min_maxTileX, min_maxTileY, progress, pos, z) VALUES ((?),(?),(?),(?),(?))", (json.dumps(min_maxTileX), json.dumps(min_maxTileY), 0, json.dumps(middle_coords), z))
        cur.execute("SELECT max(id) FROM tasks")
        task_id = cur.fetchone()[0]
        cur.close()
        db.db.commit()
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
        tasks = []
        tile1 = None
        prog_cnt = 0
        for x in range(min_maxTileX[0], min_maxTileX[1] + 1):
            for y in range(min_maxTileY[0], min_maxTileY[1] + 1):
                if prog_cnt < progress:
                    prog_cnt += 1
                    continue
                if tile1 == None:
                    tile1 = f"{x},{y}"
                else:
                    try:
                        tasks.append(asyncio.create_task(load(session, tile1, f"{x},{y}", z)))
                        tile1 = None
                        if len(tasks) >= config["map_async_level"]:
                            await asyncio.gather(*tasks)
                            tasks = []
                            cur = db.db.cursor()
                            cur.execute(f"UPDATE tasks SET progress={prog_cnt} WHERE id={task_id}")
                            cur.close()
                            db.db.commit()
                            print(f"Progress: {(prog_cnt * 100) // tiles_cnt}%")
                    except Exception as e:
                        print("######### " + str(e))
                prog_cnt += 1
    db.db.execute("DELETE FROM tasks WHERE id=(?)", (task_id,))
    db.db.commit()

thread_tasks = []
passwd_threads = []

async def get_passwords(session, bssids: list):
    headers = { 
        'Content-type': 'application/json',  
        'Accept': 'text/plain', 
        'Host': '3wifi.stascorp.com' 
    } 
    tasks = [asyncio.create_task(session.get("http://134.0.119.34/api/ajax.php?Version=0.51&Key=23ZRA8UBSLsdhbdJMp7IpbbsrDFDLuBC&Query=Find&BSSID=" + i, headers=headers)) for i in bssids]
    responses = await asyncio.gather(*tasks)
    cnt = 0
    cur = db.db.cursor()
    for resp in responses:
        resp = await resp.text()
        try:
            cur.execute("UPDATE networks SET API_ANS=(?) WHERE bssid=(?)", (resp, bssids[cnt]))
        except Exception as e:
            print("$$$$ " + str(e))
        cnt += 1
    cur.close()
    bssids.clear()
    try:
        db.db.commit()
    except:
        pass

def thread_balancer(threads_cnt, async_limit=8):
    all_queued = []
    for i in range(threads_cnt):
        for y in thread_tasks[i]:
            all_queued.append(y)
    cursor = db.db.cursor()
    cursor.execute(f"SELECT DISTINCT bssid FROM networks WHERE API_ANS IS NULL AND bssid NOT in ({str(all_queued)[1:-1]}) LIMIT {int(async_limit) * threads_cnt}")
    bssids = cursor.fetchall()
    #cursor.execute(f"UPDATE networks SET API_ANS=\"\" WHERE bssid in ({str([i[0] for i in bssids])[1:-1]})")
    cursor.close()
    thread_tasks_cnt = []
    for i in range(threads_cnt):
        thread_tasks_cnt.append(len(thread_tasks[i]))
    for i in bssids:
        min_load_ind = 0
        for y in range(len(thread_tasks_cnt)):
            if thread_tasks_cnt[y] < thread_tasks_cnt[min_load_ind]:
                min_load_ind = y
        if thread_tasks_cnt[min_load_ind] < async_limit:
            thread_tasks[min_load_ind].append(i[0])
            thread_tasks_cnt[min_load_ind] += 1

async def pool_passwords(thread_ind=0, async_limit=8):
    global map_end
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
        while not(map_end) or len(thread_tasks[thread_ind]) > 0:
            try:
                if len(thread_tasks[thread_ind]) > 0:
                    if len(thread_tasks[thread_ind]) < async_limit:
                        thread_balancer(config["pass_threads_cnt"], async_limit)
                    await get_passwords(session, thread_tasks[thread_ind])
                    thread_balancer(config["pass_threads_cnt"], async_limit)
                else:
                    thread_balancer(config["pass_threads_cnt"], async_limit)
                    await asyncio.sleep(0.2)
            except Exception as e:
                print("pool " + str(e))

def start_passwords_scan():
    global passwd_threads
    for i in range(config["pass_threads_cnt"]):
        thread_tasks.append([])
    thread_balancer(config["pass_threads_cnt"], config["pass_async_level"])
    for i in range(config["pass_threads_cnt"]):
        th = Thread(target=asyncio.run, name=f"3wifiparser{i}", args=(pool_passwords(i, config["pass_async_level"]), ))
        passwd_threads.append(th)
        th.start()

def wait4passwords_end():
    for i in passwd_threads:
        i.join()

if __name__ == "__main__":
    if config["scan_passwords"]:
        start_passwords_scan()
    asyncio.run(main())
    map_end = True
    if config["scan_passwords"]:
        wait4passwords_end()
    print("End")