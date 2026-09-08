"""Explain changes between completed scans without model requests."""
from market import stamp


def key(idea):
    return (idea['symbol'], idea['direction'])


def compare(previous, current, rejected, errors):
    before, after = {key(i):i for i in previous}, {key(i):i for i in current}
    events=[]
    for identity, idea in after.items():
        old=before.get(identity)
        reasons=idea.get('reasons',[])
        if old is None:
            events.append({'kind':'qualified','symbol':idea['symbol'],'direction':idea['direction'],
                           'message':'New qualifying idea. '+' '.join(reasons[:3]),'identity':identity})
        else:
            movement=(idea['entry']/old['entry']-1)*100 if old.get('entry') else 0
            if abs(movement)>=.5:
                events.append({'kind':'updated','symbol':idea['symbol'],'direction':idea['direction'],
                               'message':f'Entry reference moved {movement:+.2f}% since the previous scan; sizing and exit references were recalculated.', 'identity':identity})
    for identity, old in before.items():
        if identity in after:continue
        reason=next((r['reason'] for r in rejected if r['symbol'] in (old['symbol'],old.get('underlying'))),None)
        if not reason:reason=' '.join(errors[:2]) or 'No longer in the current top-ranked eligible ideas; no specific rejection was returned.'
        events.append({'kind':'withdrawn','symbol':old['symbol'],'direction':old['direction'],
                       'message':reason,'identity':identity})
    return events


class ChangeLog:
    def __init__(self):
        self.events=[]
        self.sequence=0
        self.last_alert={}

    def record(self, previous, current, rejected, errors, at):
        # Cooldown uses elapsed timestamps, independent of per-scan idea UUIDs.
        epoch=at.timestamp()
        self.last_alert={k:t for k,t in self.last_alert.items() if epoch-t<600}
        for event in compare(previous,current,rejected,errors):
            identity=event.pop('identity')
            notify=event['kind']=='qualified' and identity not in self.last_alert
            if notify:self.last_alert[identity]=epoch
            self.sequence+=1
            self.events.append(dict(event,id=self.sequence,at=stamp(at),notify=notify))
        self.events=self.events[-100:]
