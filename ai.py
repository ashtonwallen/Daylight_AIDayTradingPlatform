"""Provider adapters with a common text/source envelope. Secrets never leave server settings."""
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from market import DataError

ROOT=Path(__file__).resolve().parent
LOCK=threading.RLock()
PROVIDERS={
    'openai':('OpenAI','OPENAI_API_KEY','OPENAI_RESEARCH_MODEL','gpt-5-mini',True),
    'anthropic':('Anthropic','ANTHROPIC_API_KEY','ANTHROPIC_MODEL','',True),
    'google':('Google Gemini','GOOGLE_API_KEY','GOOGLE_MODEL','',True),
    'local':('LM Studio / local','LOCAL_AI_API_KEY','LOCAL_AI_MODEL','',False),
}


def settings():
    with LOCK:
        provider=os.getenv('AI_PROVIDER','openai')
        if provider not in PROVIDERS:provider='openai'
        label,key_var,model_var,default,web=PROVIDERS[provider]
        model=os.getenv(model_var,default)
        return {'provider':provider,'label':label,'model':model,
                'configured':bool(model and (provider=='local' or os.getenv(key_var))),
                'web_research':web,'local_url':os.getenv('LOCAL_AI_URL','http://127.0.0.1:1234/v1'),
                'providers':[{'id':p,'label':v[0],'model':os.getenv(v[2],v[3]),
                              'key_configured':bool(os.getenv(v[1])),'web_research':v[4]} for p,v in PROVIDERS.items()]}


def configured():return settings()['configured']


def local_url(value):
    try:
        u=urlparse(value)
        if u.scheme not in ('http','https') or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.query or u.fragment:
            raise ValueError()
        if u.path.rstrip('/') not in ('','/v1'):raise ValueError()
        if u.port is not None and not 1<=u.port<=65535:raise ValueError()
        return value.rstrip('/')+('/v1' if not u.path.rstrip('/') else '')
    except (TypeError,ValueError):raise ValueError('Local model URL must be a localhost HTTP(S) address ending in /v1, without credentials or query parameters.') from None


def save_settings(data):
    provider=data.get('provider')
    if not isinstance(provider,str) or provider not in PROVIDERS:raise ValueError('Choose a supported AI provider.')
    model=data.get('model','')
    if not isinstance(model,str):raise ValueError('Enter a model ID as text.')
    model=model.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,149}',model):raise ValueError('Enter a valid model ID from your provider.')
    key=data.get('api_key','')
    if not isinstance(key,str) or len(key)>500 or any(c in key for c in '\r\n\x00\"\''):
        raise ValueError('Invalid API credential format.')
    updates={'AI_PROVIDER':provider,PROVIDERS[provider][2]:model}
    if key.strip():updates[PROVIDERS[provider][1]]=key.strip()
    if data.get('clear_key') is True:updates[PROVIDERS[provider][1]]=''
    if provider=='local':updates['LOCAL_AI_URL']=local_url(data.get('local_url','http://127.0.0.1:1234/v1'))
    with LOCK:
        path=ROOT/'.env'
        lines=path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
        written=set()
        output=[]
        for line in lines:
            name=line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
            if name in updates:
                if name not in written:output.append(name+'='+updates[name]);written.add(name)
            else:output.append(line)
        output.extend(k+'='+v for k,v in updates.items() if k not in written)
        temp=ROOT/'.env.pending'
        temp.write_text('\n'.join(output)+'\n',encoding='utf-8')
        temp.replace(path)
        os.environ.update(updates)


def complete(instructions, data, web=False, domains=None, max_tokens=3000, can_send=None):
    with LOCK:
        config=settings()
        key=os.getenv(PROVIDERS[config['provider']][1],'')
    p,model=config['provider'],config['model']
    if not config['configured']:raise DataError('Configure the selected AI provider and model first.')
    if web and not config['web_research']:raise DataError('Local models have no web-search adapter. They can assess existing market data; autonomous web research is unavailable.')
    content=data if isinstance(data,str) else json.dumps(data,allow_nan=False)
    headers={'Content-Type':'application/json'}
    if p=='openai':
        endpoint='https://api.openai.com/v1/responses'
        headers['Authorization']='Bearer '+key
        payload={'model':model,'store':False,'instructions':instructions,'input':content,'max_output_tokens':max_tokens}
        if re.match(r'^(gpt-5|gpt-6|o[134])',model):payload['reasoning']={'effort':'low'}
        if web:
            tool={'type':'web_search','search_context_size':'low'}
            if domains:tool['filters']={'allowed_domains':domains}
            payload.update(tools=[tool],tool_choice='auto',max_tool_calls=5,include=['web_search_call.action.sources'])
    elif p=='anthropic':
        endpoint='https://api.anthropic.com/v1/messages'
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        payload={'model':model,'max_tokens':max_tokens,'system':instructions,'messages':[{'role':'user','content':content}]}
        if web:
            tool={'type':'web_search_20250305','name':'web_search','max_uses':5}
            if domains:tool['allowed_domains']=domains
            payload['tools']=[tool]
    elif p=='google':
        endpoint='https://generativelanguage.googleapis.com/v1beta/models/'+quote(model,safe='')+':generateContent'
        headers['x-goog-api-key']=key
        # Gemini counts internal thinking tokens against maxOutputTokens, so the caller's budget is
        # treated as the visible-answer budget and thinking is given headroom on top. Measured on
        # gemini-3.8-flash: a grounded thesis spent 5,422 thinking tokens for a 1,717-token answer.
        # Too little headroom starves the answer and truncates its JSON mid-object. The ceiling only
        # costs what is actually generated, so it is set well clear of observed thinking.
        payload={'systemInstruction':{'parts':[{'text':instructions}]},'contents':[{'role':'user','parts':[{'text':content}]}],
                 'generationConfig':{'maxOutputTokens':max_tokens+GOOGLE_THINKING_HEADROOM}}
        if web:
            payload['tools']=[{'google_search':{}}]
            if domains:payload['systemInstruction']['parts'][0]['text']+=' Search only these sites: '+', '.join(domains)+'.'
    else:
        endpoint=local_url(config['local_url'])+'/chat/completions'
        if key:headers['Authorization']='Bearer '+key
        payload={'model':model,'messages':[{'role':'system','content':instructions},{'role':'user','content':content}],
                 'max_tokens':max_tokens,'stream':False}
    try:
        if can_send and not can_send():raise DataError('AI paused before this request was sent.')
        request=Request(endpoint,data=json.dumps(payload).encode(),headers=headers,method='POST')
        with urlopen(request,timeout=90 if web else 60) as response:result=json.load(response)
        return normalize(p,result)
    except HTTPError as exc:raise DataError(f'{config["label"]} HTTP {exc.code}; check the selected model, key and account limits.') from None
    except (URLError,TimeoutError,OSError):raise DataError(f'{config["label"]} connection unavailable or timed out.') from None
    except (ValueError,TypeError,KeyError,IndexError):raise DataError(f'{config["label"]} returned an invalid response.') from None


def normalize(provider, result):
    if provider=='openai':return result
    texts,sources,reason=[],[],None
    if provider=='anthropic':
        reason=result.get('stop_reason')
        completed=reason=='end_turn'
        for block in result.get('content',[]):
            if block.get('type')=='text':
                texts.append(block.get('text',''))
                sources.extend({'url':c['url'],'title':c.get('title','')} for c in block.get('citations',[]) if c.get('url'))
            if block.get('type')=='web_search_tool_result' and isinstance(block.get('content'),list):
                sources.extend({'url':c['url'],'title':c.get('title','')} for c in block['content'] if c.get('url'))
        usage=result.get('usage',{})
    elif provider=='google':
        candidate=result.get('candidates',[{}])[0]
        reason=candidate.get('finishReason')
        completed=reason=='STOP'
        texts=[p.get('text','') for p in candidate.get('content',{}).get('parts',[]) if not p.get('thought')]
        sources=[{'url':c['web']['uri'],'title':c['web'].get('title','')} for c in candidate.get('groundingMetadata',{}).get('groundingChunks',[]) if c.get('web',{}).get('uri')]
        if not sources:
            # Some Gemini versions return groundingMetadata empty and instead emit signed
            # grounding-redirect links inline. Those opaque tokens are minted by Google's search
            # pipeline, so a fabricated one is a dead link rather than a convincing false citation.
            # Without accepting them, every sourced workflow on Gemini can only return no-conclusion.
            sources=[{'url':link,'title':'Google Search grounding redirect'} for link in
                     dict.fromkeys(re.findall(GROUNDING_REDIRECT,'\n'.join(texts)))]
        usage=result.get('usageMetadata',{})
    else:
        choice=result.get('choices',[{}])[0]
        reason=choice.get('finish_reason')
        completed=reason=='stop'
        texts=[choice.get('message',{}).get('content','')]
        usage=result.get('usage',{})
    output=[{'type':'message','content':[{'type':'output_text','text':'\n'.join(texts)}]}]
    if sources:output.insert(0,{'type':'web_search_call','action':{'sources':sources}})
    envelope={'status':'completed' if completed else 'incomplete','output':output,'usage':usage}
    if not completed:
        # Name the provider's own stop reason so an aborted reply is diagnosable, not just "incomplete".
        envelope['incomplete_details']={'reason':str(reason or 'unknown')}
    return envelope


def extract_json(text):
    """Recover one JSON object from a model reply.

    Models that search the web cannot also be pinned to a strict JSON response format, so replies
    arrive wrapped in prose, fenced code blocks, or both. Parsing the whole string is tried first;
    otherwise the first balanced object is scanned out, ignoring braces inside strings.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('the response contained no text')
    cleaned = text.strip()
    fence = re.search(r'```(?:json|JSON)?\s*(.+?)```', cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        result = json.loads(cleaned)
    except ValueError:
        start = cleaned.find('{')
        if start < 0:
            raise ValueError('the response contained no JSON object') from None
        depth, in_string, escaped, end = 0, False, False, -1
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped: escaped = False
                elif char == '\\': escaped = True
                elif char == '"': in_string = False
                continue
            if char == '"': in_string = True
            elif char == '{': depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end < 0:
            raise ValueError('the JSON object in the response was cut off, which usually means the reply hit its length limit') from None
        try:
            result = json.loads(cleaned[start:end])
        except ValueError:
            raise ValueError('the response contained text that was not valid JSON') from None
    if not isinstance(result, dict):
        raise ValueError('the response JSON was not an object')
    return result


GROUNDING_REDIRECT = r'https://vertexaisearch\.cloud\.google\.com/grounding-api-redirect/[A-Za-z0-9_\-=]+'

# Room for Gemini's internal reasoning on top of the caller's visible-answer budget.
GOOGLE_THINKING_HEADROOM = 12288

ENUM_ALIASES = {'neutral': 'mixed', 'unclear': 'mixed', 'uncertain': 'mixed', 'balanced': 'mixed',
                'insufficient': 'no-conclusion', 'inconclusive': 'no-conclusion', 'none': 'no-conclusion',
                'positive': 'bullish', 'negative': 'bearish', 'up': 'bullish', 'down': 'bearish'}


def normalize_choice(value, allowed, aliases=None):
    """Map a model's free-form label onto one of `allowed`, or None.

    Models routinely answer "Cautiously Bullish" where the schema asks for "bullish". Rejecting that
    outright throws away a usable report, so labels are matched leniently while the set of accepted
    outcomes stays fixed. Longest allowed value wins, so "cautiously-favorable" is not read as
    "favorable" and "unfavorable" is not read as "favorable".
    """
    if not isinstance(value, str):
        return None
    key = re.sub(r'[\s_]+', '-', value.strip().lower()).strip('-')
    if key in allowed:
        return key
    for candidate in sorted(allowed, key=len, reverse=True):
        if candidate in key:
            return candidate
    for alias, target in {**ENUM_ALIASES, **(aliases or {})}.items():
        if alias in key and target in allowed:
            return target
    return None


def response_text(response):
    return '\n'.join(c.get('text', '') for o in response.get('output', [])
                     for c in o.get('content', []) if c.get('type') == 'output_text').strip()


def json_result(instructions, data, can_send=None, web=False, domains=None, max_tokens=3000):
    response=complete(instructions,data,can_send=can_send,web=web,domains=domains,max_tokens=max_tokens)
    if response.get('status')!='completed':raise DataError('AI response incomplete; no validated result produced.')
    try:
        result=extract_json(response_text(response))
        json.dumps(result,allow_nan=False)
        return result
    except (ValueError,TypeError) as exc:
        raise DataError(f'AI returned invalid structured output ({exc}); no result accepted.') from None
