from pathlib import Path
import concurrent.futures,json,os,platform,socket,sqlite3,statistics,subprocess,sys,time
import httpx
root=Path.cwd();out=root/'output/api-performance-2026-11-02';data=out/'fixture/data'
db=sqlite3.connect(data/'app.db')
db.execute("UPDATE app_settings SET jellyfin_enabled=1,native_streaming_enabled=0")
db.execute("UPDATE users SET onboarding_status='completed',vault_setup_pending=0")
uid=db.execute("SELECT id FROM users WHERE username='perfadmin'").fetchone()[0]
movie=db.execute("SELECT id FROM media_titles WHERE type='movie' LIMIT 1").fetchone()[0]
series=db.execute("SELECT id FROM media_titles WHERE type='series' LIMIT 1").fetchone()[0]
db.commit();db.close()
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
log=(out/'server.log').open('wb')
server=subprocess.Popen([sys.executable,str(out/'server.py'),str(data),str(port)],stdout=log,stderr=subprocess.STDOUT)
base=f'http://127.0.0.1:{port}'
def distribution(rows):
 good=[r for r in rows if r.get('status')==200];times=sorted(r['ms'] for r in good)
 return {'n':len(rows),'successes':len(good),'errors':len(rows)-len(good),'p50_ms':round(statistics.median(times),3) if times else None,'p95_ms':round(times[max(0,__import__('math').ceil(len(times)*.95)-1)],3) if len(times)>=20 else None,'max_ms':max(times,default=None),'median_sql':statistics.median([r['sql'] for r in good if r.get('sql') is not None]) if good and all(r.get('sql') is not None for r in good) else None,'median_bytes':statistics.median([r['bytes'] for r in good]) if good else None,'samples':rows}
try:
 for i in range(300):
  try:
   if httpx.get(base+'/api/health',timeout=1).status_code==200:break
  except httpx.HTTPError:pass
  if server.poll() is not None:raise RuntimeError('server exited')
  time.sleep(.1)
 web=httpx.Client(base_url=base,timeout=30)
 r=web.post('/api/session/login',json={'username':'perfadmin','password':'perf-household-password'});r.raise_for_status()
 native=httpx.Client(base_url=base,timeout=30,headers={'Authorization':'MediaBrowser Client="Audit", Device="Synthetic", DeviceId="api-audit", Version="1"'})
 r=native.post('/Users/AuthenticateByName',json={'Username':'perfadmin','Pw':'perf-household-password'});r.raise_for_status()
 native.headers['X-Emby-Token']=r.json()['AccessToken']
 cases=[('web_navigation',web,'/api/navigation'),('web_settings',web,'/api/settings/me'),('web_library_50',web,'/api/library?limit=50'),('web_library_500',web,'/api/library?limit=500'),('web_groups',web,'/api/library/groups?kind=episode'),('web_titles_50',web,'/api/titles?type=movie&limit=50'),('web_continue',web,'/api/playback/continue'),('web_new_library',web,'/api/home/new-in-library'),('web_title_rows',web,'/api/home/title-rows'),('web_movie_detail',web,'/api/titles/'+movie),('web_series_detail',web,'/api/titles/'+series),('native_navigation',native,'/Lumina/Settings/navigation'),('native_capabilities',native,'/Lumina/Settings/capabilities'),('native_movies_50',native,'/Items?IncludeItemTypes=Movie&Recursive=true&Limit=50'),('native_movies_500',native,'/Items?IncludeItemTypes=Movie&Recursive=true&Limit=500'),('native_resume',native,f'/Users/{uid}/Items/Resume?Limit=20'),('native_home',native,'/Lumina/Home/rows')]
 result={'revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'host':{'system':platform.system(),'machine':platform.machine(),'python':platform.python_version(),'cpus':os.cpu_count(),'load_before':os.getloadavg()},'controls':{'provider':'existing serve.py canned extractor + loopback socket guard','lifespan':'off; isolates route computation; background workers NOT measured','rate_limits':'main.enforce_rate_limit disabled by serve.py; not all service rules disabled','filesystem_cache':'not flushed; warm/unknown','samples_per_phase':20,'concurrency_levels':[1,6],'wire':'real HTTP on loopback; decoded/uncompressed response bytes; no network shaping','native_fields':'default Items DTO; no extra Fields query','users':'one shared admin principal, web cookie/native token; six concurrent requests, not six independent users'},'cases':{}}
 def measure(client,path):
  started=time.perf_counter()
  try:
   response=client.get(path)
   return {'status':response.status_code,'ms':round((time.perf_counter()-started)*1000,3),'bytes':len(response.content),'sql':int(response.headers['x-perf-queries']) if 'x-perf-queries' in response.headers else None}
  except httpx.HTTPError as e:return {'error':type(e).__name__,'ms':round((time.perf_counter()-started)*1000,3)}
 for label,client,path in cases:
  first=measure(client,path)
  row={'method':'GET','path':path.replace(uid,'{user}').replace(movie,'{movie}').replace(series,'{series}'),'first':first}
  if first.get('status')==200:
   row['c1']=distribution([measure(client,path) for _ in range(20)])
   with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:row['c6']=distribution(list(pool.map(lambda _:measure(client,path),range(20))))
  result['cases'][label]=row
  (out/'measurements.json').write_text(json.dumps(result,indent=2)+'\n')
  print(label,first.get('status'),row.get('c1',{}).get('p50_ms'),row.get('c6',{}).get('p50_ms'),flush=True)
 result['host']['load_after']=os.getloadavg();(out/'measurements.json').write_text(json.dumps(result,indent=2)+'\n')
finally:
 server.terminate()
 try:server.wait(timeout=15)
 except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
 log.close()
