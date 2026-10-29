"""Tests for the 0.27.0 network write guard: the JS that the probe, kbclick and
kbopt install on every call (no request leaves the page with a method other than
GET), run in node against stubbed fetch / XMLHttpRequest / sendBeacon /
HTMLFormElement and a submit listener; the Python mirror (request_blocked,
form_blocked) agrees with the JS rule; every generated JS parses. Hermetic: no
browser, no network.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_fixtures  # noqa: F401  (puts scripts/kb on sys.path)

import kb_capture as C

NODE = shutil.which("node")

HARNESS = r"""
const vm=require('vm');const fs=require('fs');
const sc=JSON.parse(fs.readFileSync(0,'utf8'));
const log=[],listeners=[];
class XHR{open(m,u){log.push('xhr.open '+m);}send(b){log.push('xhr.send');return 'sent';}}
class Form{constructor(m,a){this.method=m;this.action=a||'/save/';}
  submit(){log.push('form.submit');return 'sub';}
  requestSubmit(s){log.push('form.requestSubmit');return 'rs';}}
const ctx={URL,log,listeners,Form,XHR,G:sc.guard,PROBE:sc.probe,CLICK:sc.click,OPT:sc.opt};
ctx.window=ctx;
ctx.location={href:'https://demo.example/app/',pathname:'/app/',search:''};
ctx.innerWidth=1470;ctx.innerHeight=802;ctx.devicePixelRatio=2;
ctx.fetch=function(i,o){log.push('fetch');return Promise.resolve('real');};
ctx.XMLHttpRequest=XHR;ctx.HTMLFormElement=Form;
ctx.navigator={sendBeacon(u,d){log.push('beacon');return true;}};
ctx.sessionStorage={getItem:()=>null};
ctx.getComputedStyle=()=>({cursor:'',backgroundColor:'rgba(0,0,0,0)'});
ctx.document={title:'',body:{innerText:'',querySelectorAll:()=>[]},querySelectorAll:()=>[],querySelector:()=>null,
  addEventListener(t,f,c){listeners.push({t,f,c});},elementFromPoint:()=>null};
vm.createContext(ctx);
if(sc.preinstall!==false)vm.runInContext(sc.guard,ctx);
(async()=>{
  let out;
  try{out=await vm.runInContext('(async()=>{'+sc.scenario+'})()',ctx);}catch(e){out={threw:String(e&&e.message||e)};}
  console.log(JSON.stringify({out,log,blocked:ctx.__kbBlocked,last:ctx.__kbLast,listeners:listeners.length}));
})();
"""


def run(scenario, preinstall=True):
    r = subprocess.run([NODE, "-e", HARNESS], capture_output=True, text=True, timeout=60,
                       input=json.dumps({"guard": C.guard_source(), "probe": C.probe_source(),
                                         "click": C.click_source(), "opt": C.option_source(),
                                         "scenario": scenario, "preinstall": preinstall}))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


METHODS_BLOCKED = ["POST", "PUT", "PATCH", "DELETE", "post", "Delete", " patch "]


@unittest.skipUnless(NODE, "node not installed")
class Fetch(unittest.TestCase):
    def test_get_passes(self):
        r = run("""
          const a=await fetch('/api/list');
          const b=await fetch('/api/list',{method:'GET'});
          const c=await fetch('/api/list',{method:'get'});
          const d=await fetch({url:'/api/list',method:'GET'});
          const e=await fetch(new URL('https://demo.example/x'));
          return [a,b,c,d,e];""")
        self.assertEqual(r["out"], ["real"] * 5)
        self.assertEqual(r["log"], ["fetch"] * 5)
        self.assertEqual((r["blocked"] or 0), 0)

    def test_writes_are_rejected_and_counted(self):
        for m in METHODS_BLOCKED:
            with self.subTest(m):
                r = run(f"""
                  try{{await fetch('/api/sessions/12/',{{method:{json.dumps(m)},body:'x'}});return 'sent';}}
                  catch(e){{return e instanceof TypeError&&e.message;}}""")
                self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
                self.assertEqual(r["log"], [])                                  # never reached the real fetch
                self.assertEqual(r["blocked"], 1)
                self.assertEqual(r["last"], f"{m.strip().upper()} /api/sessions/12/")

    def test_request_object_and_init_override(self):
        r = run("""
          const out=[];
          for(const args of [[{url:'/a',method:'POST'}],[{url:'/b',method:'GET'},{method:'PUT'}],[{url:'/c'},{method:'DELETE'}]]){
            try{await fetch(...args);out.push('sent');}catch(e){out.push('blocked');}}
          out.push(await fetch({url:'/d',method:'POST'},{method:'GET'}));       // init wins: GET
          return out;""")
        self.assertEqual(r["out"], ["blocked", "blocked", "blocked", "real"])
        self.assertEqual(r["blocked"], 3)

    def test_counter_increments_and_keeps_the_last(self):
        r = run("""
          for(const m of ['POST','PUT','PATCH'])try{await fetch('/api/'+m.toLowerCase(),{method:m});}catch(e){}
          await fetch('/api/read');
          return window.__kbBlocked;""")
        self.assertEqual((r["out"], r["blocked"], r["last"]), (3, 3, "PATCH /api/patch"))
        self.assertEqual(r["log"], ["fetch"])                                   # only the GET got through


@unittest.skipUnless(NODE, "node not installed")
class Xhr(unittest.TestCase):
    def test_get_passes_and_writes_throw(self):
        r = run("""
          const out=[];
          const go=(m,u)=>{const x=new XMLHttpRequest();x.open(m,u);try{out.push(x.send(null));}catch(e){out.push(e.message);}};
          go('GET','/api/list');go('get','/api/list');go('POST','/api/x/');go('PUT','/api/y/');go('PATCH','/z');go('DELETE','/d');
          return out;""")
        self.assertEqual(r["out"], ["sent", "sent"] + ["KB_WRITE_BLOCKED"] * 4)
        self.assertEqual(r["log"], ["xhr.open GET", "xhr.send", "xhr.open get", "xhr.send",
                                    "xhr.open POST", "xhr.open PUT", "xhr.open PATCH", "xhr.open DELETE"])
        self.assertEqual((r["blocked"], r["last"]), (4, "DELETE /d"))


@unittest.skipUnless(NODE, "node not installed")
class Beacon(unittest.TestCase):
    def test_send_beacon_returns_false(self):
        r = run("return [navigator.sendBeacon('/analytics/','x'),navigator.sendBeacon('/b')];")
        self.assertEqual(r["out"], [False, False])
        self.assertEqual((r["log"], r["blocked"], r["last"]), ([], 2, "POST /b"))


@unittest.skipUnless(NODE, "node not installed")
class Forms(unittest.TestCase):
    def test_get_form_passes_post_form_does_nothing(self):
        r = run("""
          const g=new Form('get'),p=new Form('post','/users/save/');
          return [g.submit(),p.submit(),g.requestSubmit(),p.requestSubmit(),new Form('dialog').submit(),new Form('').submit()];""")
        self.assertEqual(r["out"], ["sub", None, "rs", None, None, "sub"])      # method "" = get, as in a browser
        self.assertEqual(r["log"], ["form.submit", "form.requestSubmit", "form.submit"])
        self.assertEqual((r["blocked"], r["last"]), (3, "DIALOG /save/"))

    def test_submitter_formmethod_wins(self):
        r = run("""
          const g=new Form('get'),p=new Form('post');
          return [g.requestSubmit({formMethod:'post'}),p.requestSubmit({formMethod:'get'})];""")
        self.assertEqual(r["out"], [None, "rs"])
        self.assertEqual((r["log"], r["blocked"]), (["form.requestSubmit"], 1))

    def test_submit_event_listener(self):
        r = run("""
          const l=listeners[0];
          const ev=(m,s)=>({target:new Form(m,'/x/'),submitter:s||null,prevented:false,stopped:false,
            preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}});
          const res=[];
          for(const e of [ev('post'),ev('get'),ev('get',{formMethod:'post'}),ev('post',{formMethod:'get'}),ev('dialog')]){l.f(e);res.push(e.prevented&&e.stopped);}
          return [l.t,l.c,res];""")
        self.assertEqual(r["out"], ["submit", True, [True, False, True, False, True]])    # capture phase
        self.assertEqual((r["blocked"], r["listeners"]), (3, 1))


FRAME = r"""
const mkFrame=(origin)=>{const f={log:[],URL,
  fetch(i,o){f.log.push('fetch');return Promise.resolve('real');},
  XMLHttpRequest:class{open(m,u){f.log.push('xhr.open '+m);}send(){f.log.push('xhr.send');}},
  HTMLFormElement:class{submit(){f.log.push('form.submit');}},
  navigator:{sendBeacon(){f.log.push('beacon');return true;}},
  location:{href:origin+'/inner/'},listeners:[],
  document:{addEventListener(t,fn,c){f.listeners.push(t);},querySelectorAll:()=>[]}};
  f.window=f;return f;};
"""


@unittest.skipUnless(NODE, "node not installed")
class XhrWebSocketFrames(unittest.TestCase):
    def test_an_xhr_opened_before_the_install_is_blocked_on_send(self):
        r = run("""
          const x=new XMLHttpRequest();x.open('GET','/r');        // opened by page code before the guard ran
          eval(G);
          let msg='sent';try{x.send();}catch(e){msg=e.message;}
          const y=new XMLHttpRequest();y.open('GET','/r');y.send();   // opened after the install: a GET passes
          return msg;""", preinstall=False)
        self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
        self.assertEqual(r["log"], ["xhr.open GET", "xhr.open GET", "xhr.send"])      # only the second send ran
        self.assertEqual(r["blocked"], 1)

    def test_websocket_send_throws_and_counts(self):
        r = run("""
          window.WebSocket=class{constructor(u){this.url=u;}send(d){log.push('ws.send');}};
          eval(G);eval(G);
          let msg='sent';try{new WebSocket('wss://demo.example/live/x').send('hi');}catch(e){msg=e.message;}
          return [msg,WebSocket.prototype.send.__kb];""", preinstall=False)
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", 1])
        self.assertEqual((r["log"], r["blocked"], r["last"]), ([], 1, "WS /live/x"))

    def test_installs_into_same_origin_frames_and_skips_cross_origin(self):
        r = run(FRAME + """
          const a=mkFrame('https://demo.example'),b=mkFrame('https://demo.example');
          const cross={get document(){throw new Error('SecurityError');}};
          window.frames=[a,cross,b];window.frames.length=3;
          eval(G);
          const out=[];
          for(const f of [a,b]){try{await f.fetch('/w',{method:'POST'});out.push('sent');}catch(e){out.push(e.message);}
            const x=new f.XMLHttpRequest();x.open('PUT','/w');try{x.send();out.push('sent');}catch(e){out.push(e.message);}
            await f.fetch('/r');}
          return [out,a.__kbBlocked,b.__kbBlocked,a.log,window.__kbBlocked||0];""", preinstall=False)
        self.assertEqual(r["out"][0], ["KB_WRITE_BLOCKED"] * 4)
        self.assertEqual(r["out"][1:3], [2, 2])
        self.assertEqual(r["out"][3], ["xhr.open PUT", "fetch"])          # the POST fetch and the PUT send never ran
        self.assertEqual(r["out"][4], 0)                                   # each window counts its own writes

    def test_a_frame_added_later_is_hooked_by_the_observer_and_again_on_load(self):
        r = run(FRAME + """
          const seen=[];
          window.MutationObserver=class{constructor(cb){this.cb=cb;seen.push(this);}observe(t,o){this.t=t;this.o=o;}};
          eval(G);eval(G);                                                 // idempotent: one observer
          const child=mkFrame('https://demo.example');
          const handlers={};
          const iframe={tagName:'IFRAME',contentWindow:child,addEventListener(t,fn){handlers[t]=fn;}};
          const wrapper={tagName:'DIV',querySelectorAll:s=>s==='iframe'?[iframe]:[]};
          seen[0].cb([{addedNodes:[wrapper]}]);
          let first='sent';try{await child.fetch('/w',{method:'POST'});}catch(e){first=e.message;}
          const reload=mkFrame('https://demo.example');                    // the frame navigates: a fresh window
          iframe.contentWindow=reload;handlers.load();
          let second='sent';try{await reload.fetch('/w',{method:'DELETE'});}catch(e){second=e.message;}
          return [seen.length,seen[0].o,first,second,Object.keys(handlers)];""", preinstall=False)
        self.assertEqual(r["out"], [1, {"childList": True, "subtree": True}, "KB_WRITE_BLOCKED",
                                    "KB_WRITE_BLOCKED", ["load"]])


@unittest.skipUnless(NODE, "node not installed")
class Idempotent(unittest.TestCase):
    def test_reinstalling_wraps_nothing_twice(self):
        r = run("""
          const before=[fetch,XMLHttpRequest.prototype.open,XMLHttpRequest.prototype.send,navigator.sendBeacon,
                        Form.prototype.submit,Form.prototype.requestSubmit];
          try{await fetch('/w',{method:'POST'});}catch(e){}
          for(let i=0;i<3;i++)eval(G);
          const after=[fetch,XMLHttpRequest.prototype.open,XMLHttpRequest.prototype.send,navigator.sendBeacon,
                       Form.prototype.submit,Form.prototype.requestSubmit];
          const same=before.every((f,i)=>f===after[i]);
          const marks=after.every(f=>f.__kb===1);
          await fetch('/r');
          const x=new XMLHttpRequest();x.open('GET','/r');x.send();
          new Form('get').submit();
          return {same,marks,count:window.__kbBlocked};""")
        self.assertEqual(r["out"], {"same": True, "marks": True, "count": 1})        # the count survives re-installs
        self.assertEqual(r["log"], ["fetch", "xhr.open GET", "xhr.send", "form.submit"])   # each real call ran once
        self.assertEqual(r["listeners"], 1)

    def test_a_write_counts_once_after_reinstall(self):
        r = run("""eval(G);eval(G);try{await fetch('/w',{method:'DELETE'});}catch(e){} return window.__kbBlocked;""")
        self.assertEqual(r["out"], 1)

    def test_installs_into_a_fresh_window(self):
        r = run("return [fetch.__kb||null,Form.prototype.submit.__kb||null,window.__kbBlocked||null];", preinstall=False)
        self.assertEqual(r["out"][:2], [None, None])
        r = run("eval(G);return [fetch.__kb,Form.prototype.submit.__kb,window.__kbBlocked];", preinstall=False)
        self.assertEqual(r["out"], [1, 1, 0])

    def test_page_that_replaces_fetch_is_wrapped_again(self):
        r = run("""
          window.fetch=function(){return Promise.resolve('app');};     // an SPA swapping in its own fetch
          eval(G);
          try{await fetch('/w',{method:'POST'});return 'sent';}catch(e){return [e.message,await fetch('/r')];}""")
        self.assertEqual(r["out"], ["KB_WRITE_BLOCKED", "app"])


@unittest.skipUnless(NODE, "node not installed")
class InstalledByEveryCall(unittest.TestCase):
    def test_probe_installs_and_reports_the_count(self):
        r = run("""
          const f=(0,eval)(PROBE);
          const a={v:1,login:'/users/login/',portal:null,labels:[],side:'nav',allow:[],domain:'x.com'};
          const first=f(a);
          try{await fetch('/api/w',{method:'POST'});}catch(e){}
          const second=f(a);
          return [fetch.__kb,first.wb,second.wb,second.wl,first.h];""", preinstall=False)
        self.assertEqual(r["out"], [1, 0, 1, "POST /api/w", None])

    def test_kbclick_and_kbopt_install_even_when_they_throw(self):
        for name in ("CLICK", "OPT"):
            with self.subTest(name):
                args = {"text": "Nope", "side": "nav"} if name == "CLICK" else {"option": "Nope"}
                r = run(f"""
                  const f=(0,eval)({name});
                  try{{f({json.dumps(args)});}}catch(e){{}}
                  try{{await fetch('/api/w',{{method:'PUT'}});return 'sent';}}catch(e){{return e.message;}}""",
                        preinstall=False)
                self.assertEqual(r["out"], "KB_WRITE_BLOCKED")
                self.assertEqual(r["blocked"], 1)

    def test_every_source_carries_the_guard_and_no_placeholder(self):
        g = C.guard_source()
        for name, src in (("probe", C.probe_source()), ("click", C.click_source()), ("opt", C.option_source())):
            with self.subTest(name):
                self.assertIn(g, src)
                self.assertNotIn("__GUARD__", src)
                self.assertTrue(src.startswith("(function(a){") and src.endswith("})"))
                self.assertNotIn("//", src.replace("javascript:", "").replace("(#|", "").replace("https://", ""))

    def test_the_guard_has_no_lift_switch(self):
        g = C.guard_source()
        self.assertNotIn("a.write", g)                       # write mode never touches the network guard
        self.assertNotIn("a.", g.replace("a.v", ""))         # takes no arguments


@unittest.skipUnless(NODE, "node not installed")
class MirrorAgrees(unittest.TestCase):
    def test_request_rule(self):
        cases = [(None, None), ("GET", None), ("get", None), (" Get ", None), ("POST", None), ("put", None),
                 ("PATCH", None), ("DELETE", None), ("HEAD", None), ("OPTIONS", None), (None, "POST"),
                 (None, "GET"), ("GET", "POST"), ("POST", "GET"), ("", "DELETE")]
        scen = "const out=[];for(const [m,rq] of " + json.dumps(cases) + """){
          const i=rq?{url:'/q',method:rq}:'/q';const o=m?{method:m}:undefined;
          try{await fetch(i,o);out.push(false);}catch(e){out.push(true);}}
          return out;"""
        got = run(scen)["out"]
        self.assertEqual(got, [C.request_blocked(m, rq) for m, rq in cases])
        self.assertEqual([C.request_blocked(m) for m in ("GET", "get", "POST", "PUT", "PATCH", "DELETE", "HEAD")],
                         [False, False, True, True, True, True, True])        # strictly GET

    def test_form_rule(self):
        cases = [("get", None), ("post", None), ("GET", None), ("dialog", None), ("", None), ("get", "post"),
                 ("post", "get"), ("post", ""), ("get", "dialog")]
        scen = "const out=[];for(const [m,s] of " + json.dumps(cases) + """){
          const before=window.__kbBlocked||0;
          new Form(m).requestSubmit(s?{formMethod:s}:undefined);
          out.push((window.__kbBlocked||0)>before);}
          return out;"""
        got = run(scen)["out"]
        self.assertEqual(got, [C.form_blocked(m, s) for m, s in cases])


@unittest.skipUnless(NODE, "node not installed")
class Parses(unittest.TestCase):
    def check(self, src):
        r = subprocess.run([NODE, "--check", "-"], input=src, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_guard_and_every_source(self):
        self.check(C.guard_source())
        self.check(C.install_action()["text"])
        self.check("(" + C.probe_source() + ")")
        self.check("(" + C.click_source() + ")")
        self.check("(" + C.option_source() + ")")
        self.check(C.route_check_source(["/a/"], "/users/login/").replace("await ", "(async()=>{await ", 1) + "})()")

    def test_install_stores_all_three_functions(self):
        t = C.install_action()["text"]
        for key in ("kbp", "kbc", "kbo"):
            self.assertIn(f"sessionStorage.setItem('{key}'", t)


if __name__ == "__main__":
    unittest.main()
