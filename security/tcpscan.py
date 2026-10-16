#!/usr/bin/env python3
# Full TCP connect scan (1-65535) with asyncio. Prints open ports, and counts of closed (RST) vs filtered (timeout).
import asyncio, sys, time
host=sys.argv[1]; conc=int(sys.argv[2]) if len(sys.argv)>2 else 400; timeout=float(sys.argv[3]) if len(sys.argv)>3 else 2.0
open_,closed,filtered=[],0,0
sem=asyncio.Semaphore(conc)
async def probe(p):
    global closed,filtered
    async with sem:
        try:
            r,w=await asyncio.wait_for(asyncio.open_connection(host,p),timeout); open_.append(p); w.close()
            try: await w.wait_closed()
            except Exception: pass
        except asyncio.TimeoutError: filtered+=1
        except (ConnectionRefusedError,OSError): closed+=1
async def main():
    t=time.time(); await asyncio.gather(*(probe(p) for p in range(1,65536)))
    print(f"host={host} open={sorted(open_)} closed={closed} filtered={filtered} elapsed={time.time()-t:.0f}s")
asyncio.run(main())
