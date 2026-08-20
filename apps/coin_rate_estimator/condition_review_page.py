"""Server-rendered shell for the authenticated condition-review workspace."""

from __future__ import annotations

import html
import json

from core.market_intelligence.coin_offer_conditions import CONDITION_FAMILIES


_FAMILY_LABELS = {
    "HAS_CONDITION": "وجود شرط",
    "PAYMENT_DEADLINE": "مهلت پرداخت",
    "PAYMENT_RAIL": "روش انتقال وجه",
    "PAYMENT_ACCOUNT": "حساب یا فیش بانکی",
    "SETTLEMENT_PROCESS": "فرایند تسویه",
    "CREDIT_CHEQUE": "اعتبار یا چک",
    "DELIVERY_HANDOFF": "تحویل یا ارسال",
    "IDENTITY_ACCOUNT": "هویت یا مالک حساب",
    "QUANTITY_EXECUTION": "نحوه اجرای مقدار",
    "ITEM_QUALITY_PACKAGING": "کیفیت یا بسته‌بندی",
    "IMMEDIATE": "فوریت",
    "OTHER_EXPLICIT": "شرط صریح دیگر",
}


def render_condition_review_page(
    *,
    home_path: str,
    data_path: str,
    decision_path: str,
    logout_path: str,
    user_session: str,
) -> bytes:
    family_controls = "".join(
        "<label class='family-option'>"
        f"<input type='checkbox' name='family' value='{html.escape(family)}'>"
        f"<span>{html.escape(_FAMILY_LABELS.get(family, family))}</span>"
        "</label>"
        for family in CONDITION_FAMILIES
    )
    labels_json = json.dumps(_FAMILY_LABELS, ensure_ascii=False)
    document = f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>بازبینی شروط آفرهای سکه</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root {{ --bg:#0b1329;--surface:#111c32;--card:#17243c;--line:rgba(255,255,255,.1);--text:#f8fafc;--muted:#9aa8bd;--gold:#f59e0b;--cyan:#22d3ee;--green:#34d399;--red:#fb7185; }}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 50% -10%,#24205a 0,var(--bg) 62%);color:var(--text);font-family:Vazirmatn,Tahoma,sans-serif;line-height:1.65;min-height:100vh}}
button,input,select,textarea{{font:inherit}} .wrap{{width:min(1280px,96%);margin:18px auto 48px}}
header,.panel{{background:rgba(17,28,50,.92);border:1px solid var(--line);border-radius:20px;box-shadow:0 16px 50px rgba(0,0,0,.22)}}
header{{padding:18px 22px;display:flex;justify-content:space-between;gap:16px;align-items:center}} h1,h2,p{{margin:0}} h1{{font-size:clamp(1.25rem,2.4vw,1.8rem)}} h1 span{{color:var(--gold)}} .subtitle{{color:var(--muted);font-size:.86rem;margin-top:5px}}
.nav{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}} .btn{{border:1px solid rgba(245,158,11,.35);background:#283650;color:var(--text);border-radius:11px;padding:8px 13px;text-decoration:none;cursor:pointer}} .btn.primary{{background:linear-gradient(135deg,#d97706,#f59e0b);color:#17120a;border:0;font-weight:750}} .btn:disabled{{opacity:.45;cursor:not-allowed}}
.notice{{margin:14px 0;padding:12px 16px;border:1px solid rgba(34,211,238,.25);border-radius:14px;background:rgba(8,145,178,.09);color:#c6f7ff;font-size:.84rem}}
.toolbar{{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;align-items:end;padding:14px;margin-bottom:14px}} .tabs{{display:flex;gap:7px;flex-wrap:wrap}} .tab.active{{background:var(--gold);color:#1b1306;border-color:var(--gold);font-weight:800}} label.field{{display:grid;gap:5px;color:var(--muted);font-size:.78rem}} select,textarea,input[type=text]{{background:#0b1427;color:var(--text);border:1px solid var(--line);border-radius:10px;padding:9px 10px;outline:none}} input:focus,select:focus,textarea:focus{{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(34,211,238,.12)}}
.progress{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:14px 0}} .metric{{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:10px 12px}} .metric small{{display:block;color:var(--muted)}} .metric strong{{font-size:1.15rem;color:var(--gold)}}
.workspace{{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:14px}} .review-card{{padding:20px;min-width:0}} .queue-list{{padding:10px;max-height:76vh;overflow:auto}} .queue-item{{width:100%;text-align:right;border:1px solid var(--line);border-radius:11px;background:#111b30;color:var(--text);padding:10px;margin-bottom:7px;cursor:pointer}} .queue-item.active{{border-color:var(--gold);box-shadow:0 0 0 2px rgba(245,158,11,.14)}} .queue-item small{{display:block;color:var(--muted)}} .queue-item.reviewed::before{{content:'✓';color:var(--green);margin-left:7px}}
.sample-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}} .badge{{display:inline-flex;border-radius:999px;padding:3px 9px;background:#263553;color:#dbeafe;font-size:.74rem}} .offer-text{{margin:16px 0;background:#091326;border:1px solid rgba(245,158,11,.28);border-radius:14px;padding:16px;font-size:1.08rem;white-space:pre-wrap;overflow-wrap:anywhere}}
.blind{{padding:10px 12px;border-radius:11px;background:rgba(99,102,241,.12);color:#c7d2fe;font-size:.82rem}} .analysis{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}} .analysis-box{{border:1px solid var(--line);border-radius:12px;padding:11px;background:#111b30}} .analysis-box h3{{font-size:.9rem;margin:0 0 7px}} .analysis-box p,.analysis-box li{{font-size:.78rem;color:var(--muted)}} .tag{{display:inline-flex;margin:3px 0 3px 5px;padding:3px 7px;border-radius:8px;background:#263553;font-size:.72rem}} .tag.positive{{background:rgba(52,211,153,.15);color:#a7f3d0}} .tag.abstain{{background:rgba(245,158,11,.15);color:#fde68a}}
fieldset{{border:1px solid var(--line);border-radius:12px;margin:12px 0;padding:11px}} legend{{color:var(--gold);font-size:.8rem;padding:0 6px}} .choices,.families{{display:flex;gap:8px;flex-wrap:wrap}} .choices label,.family-option{{display:flex;gap:6px;align-items:center;background:#101b30;padding:6px 9px;border-radius:9px;font-size:.78rem}} .form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .wide{{grid-column:1/-1}} textarea{{width:100%;min-height:60px;resize:vertical}} .actions{{display:flex;gap:8px;justify-content:flex-end;align-items:center}} .result{{margin-left:auto;font-size:.8rem;color:var(--cyan)}} .result.error{{color:var(--red)}}
.pager{{display:flex;justify-content:space-between;gap:8px;margin-top:10px}} .empty{{padding:54px 18px;text-align:center;color:var(--muted)}}
@media(max-width:900px){{.toolbar{{grid-template-columns:1fr 1fr}}.tabs{{grid-column:1/-1}}.workspace{{grid-template-columns:1fr}}.queue-list{{max-height:250px;order:-1}}.progress{{grid-template-columns:1fr 1fr}}}}
@media(max-width:560px){{header{{align-items:flex-start;flex-direction:column}}.toolbar,.form-grid,.analysis{{grid-template-columns:1fr}}.wide{{grid-column:auto}}.progress{{grid-template-columns:1fr 1fr}}.review-card{{padding:14px}}}}
</style></head><body><main class="wrap">
<header><div><h1>بازبینی <span>شروط آفرهای سکه</span></h1><p class="subtitle">مجموعهٔ نهایی، تحلیل زندهٔ مدل و اصلاح مالک در یک محیط خصوصی</p></div><nav class="nav"><span class="badge">{html.escape(user_session)}</span><a class="btn" href="{html.escape(home_path)}">داشبورد تخمین</a><a class="btn" href="{html.escape(logout_path)}">خروج</a></nav></header>
<p class="notice">این صفحه پژوهشی است. اصلاحات ذخیره می‌شوند اما تا یک فرایند آموزش و ارتقای جداگانه، روی ثبت آفر، تخمین نرخ یا تصمیم معاملاتی اثر ندارند. پیش‌بینی نمونه‌های ۲۴۰تایی تا ثبت حکم مستقل پنهان می‌ماند.</p>
<section class="panel toolbar"><div class="tabs"><button class="btn tab active" data-queue="SEALED">مجموعهٔ ۲۴۰تایی</button><button class="btn tab" data-queue="LIVE">آفرهای زنده</button><button class="btn tab" data-queue="REVIEWED">بررسی‌شده‌ها</button></div><label class="field">وضعیت<select id="status"><option value="ALL">همه</option><option value="PENDING">بررسی‌نشده</option><option value="REVIEWED">بررسی‌شده</option></select></label><label class="field">گروه<select id="group"><option value="ALL">هر دو گروه</option><option value="group_1">گروه ۱</option><option value="group_2">گروه ۲</option></select></label></section>
<section id="progress" class="progress"></section>
<section class="workspace"><article id="review" class="panel review-card"><div class="empty">در حال خواندن صف بازبینی…</div></article><aside class="panel queue-list"><div id="items"></div><div class="pager"><button id="newer" class="btn">قبلی</button><button id="older" class="btn">بعدی</button></div></aside></section>
</main><script>
const DATA_PATH={json.dumps(data_path)},DECISION_PATH={json.dumps(decision_path)},FAMILY_LABELS={labels_json};
let queue='SEALED',offset=0,limit=20,payload=null,currentIndex=0,formDirty=false;
const $=s=>document.querySelector(s), fmt=n=>new Intl.NumberFormat('fa-IR').format(Number(n||0));
function el(tag,cls,text){{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x}}
function metaText(item){{return `${{item.group_code==='group_1'?'گروه ۱':'گروه ۲'}} · ${{item.settlement_term}} · ${{item.session_phase}}`}}
function renderProgress(p){{const data=[['پیشرفت مجموعهٔ نهایی',`${{fmt(p.sealed_reviewed)}} / ${{fmt(p.sealed_total)}}`],['زندهٔ قابل مشاهده',fmt(p.live_visible)],['اصلاح زنده',fmt(p.live_reviewed)],['وضعیت مدل',payload.model_status==='READY_RESEARCH_SHADOW'?'آمادهٔ shadow':'قواعد / مدل غیرفعال']];$('#progress').replaceChildren(...data.map(([a,b])=>{{const d=el('div','metric');d.append(el('small','',a),el('strong','',b));return d}}))}}
function renderList(){{const root=$('#items');root.replaceChildren();payload.items.forEach((item,i)=>{{const b=el('button','queue-item'+(i===currentIndex?' active':'')+(item.review?' reviewed':''));b.type='button';b.append(el('strong','',item.private_offer_text.slice(0,62)),el('small','',metaText(item)));b.onclick=()=>{{currentIndex=i;renderList();renderCurrent()}};root.append(b)}});if(!payload.items.length)root.append(el('div','empty','موردی با این فیلتر پیدا نشد.'));$('#newer').disabled=offset===0;$('#older').disabled=offset+limit>=payload.total}}
function tags(values,kind=''){{const box=el('div');for(const value of values||[])box.append(el('span','tag '+kind,FAMILY_LABELS[value]||value));return box}}
function renderAnalysis(item,root){{if(item.analysis_blinded_until_review){{root.append(el('p','blind','برای حفظ ارزیابی کور، تحلیل مدل بعد از ذخیرهٔ حکم شما نمایش داده می‌شود.'));return}}const a=item.analysis;if(!a)return;const grid=el('div','analysis');const rules=el('section','analysis-box');rules.append(el('h3','','تحلیل قاعده‌ای'));rules.append(el('p','',a.rule.has_condition?'شرط تشخیص داده شد':'شرط صریح تشخیص داده نشد'));rules.append(tags(a.rule.condition_families));if(a.rule.condition_text)rules.append(el('p','',`قطعهٔ شرط: ${{a.rule.condition_text}}`));const model=el('section','analysis-box');model.append(el('h3','','تحلیل مدل shadow'));model.append(el('p','',a.model.status));const labels=a.model.labels||{{}};for(const [key,value] of Object.entries(labels)){{if(['POSITIVE','QUALITY_BLOCKED_POSITIVE','ABSTAIN'].includes(value.decision)){{const positive=value.decision==='POSITIVE',label=positive?'مثبت':value.decision==='ABSTAIN'?'مکث':'مثبت آزمایشیِ مسدود';const t=el('span','tag '+(positive?'positive':'abstain'),`${{FAMILY_LABELS[key]||key}} · ${{label}}${{value.probability==null?'':` · ${{Math.round(value.probability*100)}}٪`}}`);model.append(t)}}}}grid.append(rules,model);root.append(grid)}}
function renderCurrent(){{const root=$('#review');root.replaceChildren();const item=payload.items[currentIndex];formDirty=false;if(!item){{root.append(el('div','empty','موردی برای نمایش وجود ندارد.'));return}}const head=el('div','sample-head');const copy=el('div');copy.append(el('h2','',`نمونه ${{fmt(offset+currentIndex+1)}} از ${{fmt(payload.total)}}`),el('p','subtitle',metaText(item)));head.append(copy,el('span','badge',item.review?'بررسی‌شده':'در انتظار'));root.append(head,el('div','offer-text',item.private_offer_text));renderAnalysis(item,root);const form=el('form');form.id='review-form';form.innerHTML=`<fieldset><legend>حکم مستقل شما</legend><div class="choices"><label><input type="radio" name="owner_status" value="CONDITIONAL" required> شرط‌دار</label><label><input type="radio" name="owner_status" value="UNCONDITIONAL"> بدون شرط</label><label><input type="radio" name="owner_status" value="AMBIGUOUS"> مبهم</label></div></fieldset><fieldset><legend>خانواده‌های شرط</legend><div class="families">{family_controls}</div></fieldset><div class="form-grid"><label class="field">نوع تسویه<select name="owner_settlement" required><option value="">انتخاب کنید</option><option value="CASH">نقد حاضر</option><option value="TOMORROW">فردایی</option><option value="UNKNOWN">نامشخص</option></select></label><label class="field">مهلت دقیق<input type="text" name="owner_deadline" placeholder="مثلاً 14:00 یا AMBIGUOUS"></label><label class="field wide">عبارت شرط<textarea name="owner_condition_text" maxlength="512" placeholder="مثال: تک حساب؛ شب حساب — چند شرط را با «؛» یا «|» جدا کنید"></textarea></label></div><div class="actions"><span id="result" class="result" aria-live="polite"></span><button class="btn primary" type="submit">ذخیره و نمایش تحلیل</button></div>`;root.append(form);const r=item.review;if(r){{form.querySelector(`[name=owner_status][value="${{r.owner_status}}"]`).checked=true;for(const family of r.owner_families||[]){{const c=form.querySelector(`[name=family][value="${{family}}"]`);if(c)c.checked=true}}form.owner_settlement.value=r.owner_settlement||'';form.owner_deadline.value=r.owner_deadline||'';form.owner_condition_text.value=r.owner_condition_text||''}}else form.owner_settlement.value=item.settlement_term==='CASH'||item.settlement_term==='TOMORROW'?item.settlement_term:'UNKNOWN';form.addEventListener('change',()=>{{const status=form.owner_status.value;const disabled=status!=='CONDITIONAL';form.querySelectorAll('[name=family]').forEach(x=>{{x.disabled=disabled;if(disabled)x.checked=false}});if(disabled)form.owner_condition_text.value=''}});form.addEventListener('input',()=>{{formDirty=true}});form.dispatchEvent(new Event('change'));formDirty=false;form.onsubmit=saveReview}}
async function saveReview(event){{event.preventDefault();const form=event.currentTarget,item=payload.items[currentIndex],result=form.querySelector('#result'),savedDigest=item.sample_digest,priorIndex=currentIndex;const body={{sample_digest:item.sample_digest,expected_revision:item.review?.review_revision||0,owner_status:form.owner_status.value,owner_families:[...form.querySelectorAll('[name=family]:checked')].map(x=>x.value),owner_settlement:form.owner_settlement.value,owner_condition_text:form.owner_condition_text.value.trim(),owner_deadline:form.owner_deadline.value.trim()}};result.className='result';result.textContent='در حال ذخیره…';try{{const response=await fetch(DECISION_PATH,{{method:'POST',headers:{{'Content-Type':'application/json','X-Requested-With':'condition-review'}},body:JSON.stringify(body)}});const answer=await response.json();if(!response.ok)throw new Error(answer.error||'ثبت انجام نشد');result.textContent='ذخیره شد';await load(false);const savedIndex=payload.items.findIndex(x=>x.sample_digest===savedDigest);if(savedIndex>=0){{const next=payload.items.findIndex((x,i)=>i>savedIndex&&!x.review);currentIndex=next>=0?next:savedIndex}}else currentIndex=Math.min(priorIndex,Math.max(0,payload.items.length-1));renderList();renderCurrent()}}catch(error){{result.className='result error';result.textContent=error.message}}}}
async function load(reset=true){{if(reset){{offset=0;currentIndex=0}}const params=new URLSearchParams({{queue,status:$('#status').value,group:$('#group').value,offset:String(offset),limit:String(limit)}});const response=await fetch(`${{DATA_PATH}}?${{params}}`,{{cache:'no-store'}});payload=await response.json();if(!response.ok)throw new Error(payload.error||'خواندن صف ممکن نشد');renderProgress(payload.progress);renderList();renderCurrent()}}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');queue=b.dataset.queue;load()}});$('#status').onchange=()=>load();$('#group').onchange=()=>load();$('#newer').onclick=()=>{{offset=Math.max(0,offset-limit);currentIndex=0;load(false)}};$('#older').onclick=()=>{{offset+=limit;currentIndex=0;load(false)}};load().catch(error=>$('#review').replaceChildren(el('div','empty',error.message)));setInterval(()=>{{if(queue==='LIVE'&&!formDirty)load(false).catch(()=>{{}})}},30000);
</script></body></html>"""
    return document.encode("utf-8")
