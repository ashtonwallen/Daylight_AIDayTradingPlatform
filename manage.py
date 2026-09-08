"""Local dashboard controls. This script never reads or prints API credentials."""
import argparse
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['status','start','stop','shutdown'])
    parser.add_argument('--live',action='store_true',help='Use real market data when starting')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    base=f'http://127.0.0.1:{args.port}'
    try:
        with urlopen(base+'/api/state',timeout=10) as response: state=json.load(response)
        if state.get('application')!='daylight':
            raise ValueError('This port is not running the current Daylight server.')
        if args.action!='status':
            payload=state['config'] if args.action=='start' else {}
            if args.action=='start' and args.live: payload['mode']='live'
            request=Request(base+'/api/'+args.action,data=json.dumps(payload).encode(),
                            headers={'Content-Type':'application/json','X-CSRF-Token':state['csrf']})
            with urlopen(request,timeout=10) as response: json.load(response)
            print(f'Daylight {args.action}: OK')
            return
        print(json.dumps({k:state[k] for k in ('version','running','config','last_scan','connections','ai_usage','feed_health')},indent=2))
    except HTTPError as exc:
        try: message=json.load(exc).get('error',f'HTTP {exc.code}')
        except (ValueError,AttributeError): message=f'HTTP {exc.code}'
        parser.exit(1,message+'\n')
    except (URLError,TimeoutError,OSError,ValueError) as exc:
        parser.exit(1,f'Daylight control unavailable: {exc}\n')


if __name__=='__main__': main()
