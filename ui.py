"""HTML for the blood-test desk (dashboard) and the customer booking page. Server-rendered, no framework, light + dark."""
import csv, datetime as dt, html, io, json, re, urllib.parse

e = lambda s: html.escape("" if s is None else str(s), quote=True)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STATUS = {"sheet": ("Legacy (sheet)", "mute"), "to_send": ("To send", "info"), "awaiting": ("Awaiting booking", "info"), "followup": ("Needs follow-up", "warn"),
          "booked": ("Booked", "ok"), "check": ("Check with Redcliffe", "warn"), "collected": ("Sample collected", "ok"),
          "report": ("Report ready", "ok"), "upcoming": ("Upcoming", "mute"), "missed": ("Missed window", "bad"),
          "untracked": ("Before tracking", "mute"), "cancelled": ("Cancelled", "bad"), "closed": ("Order cancelled", "mute"),
          "handed": ("Tests with linked user", "mute")}
ASK = {"resched": "Wants to reschedule"} # open button taps, for the ops team
NEXT = {"resched": "They opened the reschedule page but the booking hasn't moved yet. Call them and use Reschedule here if needed."}
MSG = {"t1": "Template 1 (welcome + book)", "t2": "Template 2 (reminder)", "thanks": "Thank-you (after Yes)", "booked": "Booking confirmation",
       "rescheduled": "Reschedule confirmation (old)", "nudge": "Booking reminder (day 3 / 5 / 7)", "custom": "Personalised test (book)", "cancelled": "Cancellation",
       "collected": "Sample collected", "report": "Report link", "t1b": "Template 1b (book Test 2)"}


ICON_CHAT = "<svg width=18 height=18 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round aria-hidden=true><path d='M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z'/><path d='M8.5 10.5h7M8.5 13.5h4'/></svg>"
ICON_PHONE = "<svg width=18 height=18 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round aria-hidden=true><path d='M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z'/></svg>"
ICON_OUT = "<svg width=18 height=18 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round aria-hidden=true><path d='M7 17L17 7M9 7h8v8'/></svg>"
ICON_BACK = "<svg width=16 height=16 viewBox='0 0 24 24' fill=none stroke=currentColor stroke-width=2 stroke-linecap=round stroke-linejoin=round aria-hidden=true><path d='M15 18l-6-6 6-6'/></svg>"


def msg_name(r): return MSG["t1b"] if r.get("note") == "t1" and r.get("test_no") == 2 else MSG.get(r.get("note"), r.get("note"))


_PANEL = ("The test covers these key health parameters:\n1) Lipid Profile\n2) Blood Sugar Profile\n3) Liver Function Profile\n4) CBC (Complete Blood Count)\n"
          "5) TSH (Thyroid Stimulating Hormone)\n\n")
_FAST = "⚠️ Please note: 12 hours of fasting is required before sample collection. You may drink water, but avoid food and other beverages.\n\n"
_CONF = ("Hello {{first_name}}\n\nHope you are doing well.\n\nThank you for confirming your availability for the blood test.\n\nThe home collection of your blood "
         "sample is scheduled for {{date}}, between {{slot}}.\n\nA certified phlebotomist will visit your provided address during your selected time slot.\n\n"
         "Please Note:\n- 12 hours of fasting is required prior to the test.\n- You may drink water during the fasting period.\n\nIf you have any questions or "
         "need to reschedule, feel free to reach out.\n\nWarm regards,\nYour Wellness Partner\nSova Health")
TEMPLATE_TEXT = { # settings key -> (label, category and buttons, body as submitted to Meta in Briza)
    "t1_template": ("Template 1: welcome + book (Test 1, the day after the order)", "Utility; button Book blood test -> booking page",
                    "Hi {{first_name}},\n\nWelcome to Sova! Thank you for starting your {{program}} program with us.\n\nThis program includes a Panel 3 Blood Test "
                    "which is linked to your diet program.\n\n" + _PANEL + _FAST + "Kindly book your test by {{book_by}} using the button below.\n\nThank you,\nTeam Sova Health"),
    "t1_next_template": ("Template 1b: book the follow-up test (Test 2)", "Utility; button Book blood test -> booking page",
                         "Hi {{first_name}},\n\nYour Sova program includes a follow-up Panel 3 Blood Test, so we can track your progress.\n\n" + _PANEL + _FAST +
                         "Kindly book your test by {{book_by}} using the button below.\n\nThank you,\nTeam Sova Health"),
    "nudge_template": ("Booking reminder: day 3, 5 and 7 while not booked", "Utility; button Book blood test -> booking page",
                       "Hi {{first_name}},\n\nThis is a gentle reminder to book your home blood test, which is part of your Sova program. Please book it by "
                       "{{book_by}} using the button below. A phlebotomist will visit your home at the time you choose.\n\n" + _FAST + "Thank you,\nTeam Sova Health"),
    "custom_template": ("Personalised test (sent from a customer's page)", "Utility; button Book blood test -> booking page",
                        "Hi {{first_name}},\n\nYour personalised blood test is here! The Sova team has set up your {{test_name}}, with sample collection at "
                        "your home.\n\nPlease book it by {{book_by}} using the button below.\n\n" + _FAST + "Thank you,\nTeam Sova Health"),
    "cancelled_template": ("Cancellation (dashboard or Redcliffe cancelled the booking)", "Utility; button Book again -> booking page",
                           "Hi {{first_name}},\n\nYour home blood sample collection scheduled for {{date}}, between {{slot}} has been cancelled.\n\n"
                           "To book a new slot, please tap the button below. If you have any questions, feel free to reach out.\n\nThank you,\nTeam Sova Health"),
    "t2_template": ("Template 2: reminder, the day before", "Utility; link buttons Yes -> /c/ (thank-you) and Reschedule -> booking page",
                    "Hi {{first_name}},\n\nThis is a reminder that your home blood sample collection is scheduled for tomorrow, {{date}}, between {{slot}}.\n\n"
                    "Please keep 12 hours of fasting before the visit. Tap Yes to confirm, or Reschedule to pick a new time."),
    "thanks_template": ("After Yes", "Utility", _CONF),
    "booked_template": ("Booking confirmation (also sent after every reschedule, with the new slot)", "Utility", _CONF),
    "rescheduled_template": ("Reschedule confirmation (no longer sent: the booking confirmation goes instead)", "Utility", "Hi {{first_name}}, your Redcliffe Labs "
                             "home blood sample collection has been rescheduled from {{old_slot}} to {{date}}, between {{slot}}.\n\nPlease keep 8-10 hours of fasting "
                             "before the visit.\n\nTeam Sova"),
    "collected_template": ("Sample collected", "Utility", "Hi {{first_name}}, your blood sample has been collected by Redcliffe Labs. Your test report will be "
                           "shared with you here on WhatsApp in 3-4 days.\n\nTeam Sova"),
    "report_template": ("Report ready", "Utility; button View report -> /r/ (Open / Download page, no code)", "Hi {{first_name}}, your blood test report is "
                        "ready.\n\nTap View report below to see it. The link works until {{valid_till}}.\n\nTeam Sova"),
    "otp_template": ("OTP", "Authentication (Meta requires it for codes); copy-code button", "<code> is your verification code. For your security, do not share "
                     "this code. This code expires in 10 minutes."),
}
NEXT_STAGE = {"booked": "Next stage (test): sample collected", "check": "Next stage (test): sample collected", "collected": "Next stage (test): report ready",
              "report": "Next stage (test): open Test 2"}
CANCEL_REASONS = ("Customer asked to cancel", "Customer not available on the date", "Address not serviceable", "Duplicate booking", "Other")
FILTERS = (("", "All"), ("asked", "Customer asked"), ("todo", "To book"), ("followup", "Follow-up"), ("booked", "Booked"), ("done", "Done"),
           ("issues", "Issues"), ("upcoming", "Upcoming"), ("sheet", "Legacy (sheet)"), ("other", "Tested on other platform"))
GROUP = {"todo": ("to_send", "awaiting", "followup"), "booked": ("booked",), "done": ("collected", "report"), "followup": ("followup",),
         "issues": ("missed", "check", "cancelled"), "upcoming": ("upcoming",)}
CSS = """
:root{--r-sm:8px;--r:10px;--r-lg:12px;--pill:999px;--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:20px;--s6:24px;--s8:32px;--wmax:1240px;--sh1:0 1px 2px rgba(16,38,46,.05);--sh2:0 10px 28px rgba(16,38,46,.14)}
:root{--bg:#f4f8f9;--card:#fff;--fg:#10262e;--mute:#566b73;--line:#d8e3e7;--pri:#0e7490;--on:#fff;--ok:#047857;--okb:#e3f5ee;--warn:#9a4a07;
--warnb:#fdf0dc;--bad:#b42318;--badb:#fde8e6;--info:#1e4fb8;--infob:#e6eefc;--muteb:#edf2f4;--ring:#0e7490}
@media (prefers-color-scheme:dark){:root{--bg:#0b1518;--card:#111e22;--fg:#e2edf0;--mute:#9cb0b7;--line:#25373d;--pri:#38bdd8;--on:#05252d;
--ok:#4ade9b;--okb:#0f2e24;--warn:#f6b562;--warnb:#33240f;--bad:#ff8a80;--badb:#3a1714;--info:#9cc0ff;--infob:#15233d;--muteb:#1a292e;--ring:#38bdd8;--sh1:0 1px 2px rgba(0,0,0,.3);--sh2:0 10px 28px rgba(0,0,0,.5)}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--pri)}a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--ring);outline-offset:2px}
header.top{position:sticky;top:0;z-index:30;background:var(--card);border-bottom:1px solid var(--line);box-shadow:var(--sh1)}
header.top .hd{max-width:var(--wmax);margin:0 auto;display:flex;align-items:center;gap:var(--s3);padding:var(--s3) var(--s5) var(--s1)}
.brand{display:flex;align-items:center;gap:10px;color:var(--fg);text-decoration:none;min-width:0}.brand img{height:30px;display:block}
.brand .mk{width:32px;height:32px;border-radius:var(--r-sm);background:var(--pri);color:var(--on);display:grid;place-items:center;font-weight:700;font-size:16px;flex:none}
.brand b{font-size:16px;line-height:1.15;display:block;letter-spacing:-.01em}.brand small{display:block;color:var(--mute);font-size:12px;line-height:1.25}
header.top .hd>.chip{margin-left:var(--s1)}.who{margin-left:auto;display:flex;align-items:center;gap:10px;font-size:13px;color:var(--mute);min-width:0}
.who .av{width:32px;height:32px;border-radius:50%;background:var(--muteb);color:var(--fg);display:grid;place-items:center;font-weight:600;flex:none;text-transform:uppercase}
.who .em{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.who a.out{color:var(--fg);text-decoration:none;border:1px solid var(--line);border-radius:var(--pill);padding:5px 14px;white-space:nowrap;font-weight:500}.who a.out:hover{background:var(--muteb)}
.navrow{max-width:var(--wmax);margin:0 auto;display:flex;align-items:center;gap:var(--s1);padding:0 9px}
nav.tabs{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;flex:1;min-width:0}nav.tabs::-webkit-scrollbar{display:none}
nav.tabs a,.dl summary{padding:10px 11px 11px;color:var(--mute);text-decoration:none;font-size:14px;font-weight:500;border-bottom:2px solid transparent;white-space:nowrap;cursor:pointer}
nav.tabs a:hover,.dl summary:hover{color:var(--fg)}nav.tabs a[aria-current=page]{color:var(--pri);border-bottom-color:var(--pri);font-weight:600}
nav.tabs .sep{width:1px;background:var(--line);margin:11px 6px;flex:none}
.dl{position:relative;flex:none}.dl summary{list-style:none;display:block}.dl summary::-webkit-details-marker{display:none}.dl summary:after{content:" \u25be";font-size:11px}
.dl[open] summary{color:var(--fg)}.dl div{position:absolute;right:0;top:calc(100% + 4px);background:var(--card);border:1px solid var(--line);border-radius:var(--r);
box-shadow:var(--sh2);padding:6px;min-width:220px;display:grid;z-index:40}.dl div a{padding:9px 10px;border-radius:var(--r-sm);text-decoration:none;color:var(--fg);font-size:14px}
.dl div a:hover{background:var(--muteb)}
@media (max-width:640px){.btc .f{flex:1 1 100%}.btc .f>input,.btc .f>select{min-width:0;width:100%}nav.tabs{-webkit-mask-image:linear-gradient(90deg,#000 85%,transparent);mask-image:linear-gradient(90deg,#000 85%,transparent)}.who .em,.brand small{display:none}header.top .hd{padding:10px var(--s4) var(--s1)}.navrow{padding:0 5px}main{padding:var(--s5) var(--s4) var(--s8)}}
main{max-width:var(--wmax);margin:0 auto;padding:var(--s6) var(--s5) 64px}h1{font-size:24px;line-height:1.25;font-weight:700;letter-spacing:-.01em;margin:0 0 var(--s4)}h2{font-size:17px;line-height:1.3;margin:0 0 var(--s3)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r-lg);padding:var(--s5);box-shadow:var(--sh1)}main>section.card+section.card{margin-top:var(--s4)}
.grid{display:grid;gap:12px}.k{grid-template-columns:repeat(auto-fit,minmax(118px,1fr))}.fill input{width:100%}.q{grid-template-columns:repeat(auto-fit,minmax(260px,1fr));margin:14px 0}
.kpi .n{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}.kpi .l{color:var(--mute);font-size:13px}.kpi a{text-decoration:none;color:inherit}
.chip{display:inline-block;padding:2px 9px;border-radius:var(--pill);font-size:13px;font-weight:600;white-space:nowrap}
.ok{background:var(--okb);color:var(--ok)}.warn{background:var(--warnb);color:var(--warn)}.bad{background:var(--badb);color:var(--bad)}
.info{background:var(--infob);color:var(--info)}.mute{background:var(--muteb);color:var(--mute)}
.m{color:var(--mute)}.small{font-size:13px}.banner{padding:var(--s3) var(--s4);border-radius:var(--r);margin:0 0 var(--s4);border-left:4px solid currentColor;background:var(--infob);color:var(--info);font-weight:500}
.banner.bad{background:var(--badb);color:var(--bad)}.banner.warn{background:var(--warnb);color:var(--warn)}.banner.okb{background:var(--okb);color:var(--ok)}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:var(--r-lg);background:var(--card);box-shadow:var(--sh1)}
table{border-collapse:collapse;width:100%;min-width:900px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}
th{font-size:12px;color:var(--mute);font-weight:600;text-transform:uppercase;letter-spacing:.04em;background:var(--muteb);position:sticky;top:0}tbody tr:hover td{background:color-mix(in srgb,var(--muteb) 55%,transparent)}tr:last-child td{border-bottom:0}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:16px 0 10px}.filters a:hover{border-color:var(--mute)}.filters a{padding:5px 12px;border-radius:var(--pill);border:1px solid var(--line);text-decoration:none;color:var(--fg);font-size:14px}
.filters a[aria-current]{background:var(--pri);color:var(--on);border-color:var(--pri)}
input,select,textarea{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:var(--r-sm);padding:8px 10px;min-height:40px}input:hover,select:hover,textarea:hover{border-color:var(--mute)}input:focus,select:focus,textarea:focus{border-color:var(--pri)}
button,.btn{font:inherit;font-weight:600;border:1px solid var(--pri);background:var(--pri);color:var(--on);border-radius:var(--r-sm);padding:8px 14px;min-height:40px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
button.ghost,.btn.ghost{background:transparent;color:var(--pri);border-color:var(--line)}button:disabled{opacity:.6;cursor:wait}
button.danger{background:var(--bad);border-color:var(--bad);color:var(--on)}summary.btn{list-style:none;width:max-content}summary.btn::-webkit-details-marker{display:none}
button:not(:disabled):hover,.btn:hover{filter:brightness(1.08)}button.ghost:hover,.btn.ghost:hover{background:var(--muteb);filter:none}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.m0{margin:0}.full{width:100%}.logo{height:28px}
.field{display:grid;gap:6px}.adduser .stack{display:grid;gap:6px}.adduser .stack>*+*{margin-top:0}.adduser .stack>input,.adduser .stack>select,.adduser .stack>textarea{width:100%}.field .row{flex-wrap:nowrap}.field input{flex:1;min-width:0}.grp{margin:12px 0 6px;font-size:13px;font-weight:600;color:var(--mute)}
.book .opt.slots{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}.book .slots label{justify-content:flex-start;padding:8px 10px}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.btc{display:flex;flex-wrap:wrap;gap:var(--s3) var(--s4);align-items:flex-end;margin:6px 0 var(--s3)}.btc .f{display:grid;gap:6px}.btc .f>span,.btc .f>label{font-size:12px;font-weight:600;color:var(--mute)}.btc .f>input,.btc .f>select{min-width:220px}mark{background:var(--warnb);color:var(--warn);padding:0 3px;border-radius:4px}.stack>*+*{margin-top:10px}
ul.list{list-style:none;margin:0;padding:0}ul.list li{padding:8px 0;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
ul.list li:first-child{border-top:0}label{font-weight:600;font-size:14px}fieldset{border:0;padding:0;margin:0}legend{font-weight:600;font-size:14px;padding:0;margin-bottom:6px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}dt{color:var(--mute)}dd{margin:0}
.book{max-width:560px}.book .opt{display:flex;flex-wrap:wrap;gap:8px}.book .opt a,.book .opt label{min-height:44px;display:inline-flex;align-items:center;padding:8px 14px;
border:1px solid var(--line);border-radius:var(--r);background:var(--card);text-decoration:none;color:var(--fg);font-weight:500;cursor:pointer}
.book .opt a[aria-current],.book .opt input:checked+span{color:var(--pri);font-weight:700}.book .opt label:has(input:checked),.book .opt a[aria-current]{border:2px solid var(--pri)}
.radio{display:flex;gap:10px;align-items:flex-start;font-weight:500;margin:8px 0}.radio input{min-height:0;margin-top:4px}.book .opt input{width:18px;min-height:18px;margin:0 8px 0 0;accent-color:var(--pri)}.book button{width:100%;min-height:48px;justify-content:center;font-size:16px}
.book input[type=text],.book input[type=number]{width:100%}.book .card>.btn{display:flex;width:100%;min-height:48px;justify-content:center;font-size:16px}.book input[type=search]{flex:1;min-width:0}.book #find{width:auto;flex:none}
.sheet{position:fixed;inset:0;z-index:50;background:var(--bg)}.sheet[hidden]{display:none}
.sheet-in{height:100%;max-width:640px;margin:0 auto;display:flex;flex-direction:column;gap:10px;padding:12px 16px max(12px,env(safe-area-inset-bottom))}
.sheet .mapwrap{flex:1;height:auto;min-height:220px}#picked{height:2.9em;line-height:1.45;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}.sheet-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.sheet-top h2{margin:0}.book #sheet-x{width:auto;flex:none}
.mapwrap{position:relative;height:280px;border-radius:var(--r-lg);overflow:hidden;border:1px solid var(--line);background:var(--muteb)}#map{position:absolute;inset:0}
.lochelp{max-width:420px;width:calc(100% - 32px);border:1px solid var(--line);border-radius:var(--r-lg);background:var(--card);color:var(--fg);padding:18px}
.lochelp::backdrop{background:rgba(0,0,0,.45)}.lochelp h2{margin:0 0 8px}.lochelp button{width:100%;justify-content:center}
.pin{position:absolute;left:50%;top:50%;width:30px;height:42px;transform:translate(-50%,-100%);pointer-events:none;z-index:2;filter:drop-shadow(0 2px 2px rgba(0,0,0,.35))}
@media (prefers-reduced-motion:no-preference){button,.btn,.filters a{transition:background .15s,color .15s}}

.back{display:inline-flex;align-items:center;gap:6px;text-decoration:none;font-size:14px}
.phead{display:flex;flex-wrap:wrap;align-items:center;gap:16px 24px;margin:12px 0 16px}.phead h1{margin:0}
.who_{display:flex;align-items:center;gap:16px;flex:1 1 380px;min-width:0}
.avatar{width:56px;height:56px;border-radius:50%;background:var(--infob);color:var(--pri);display:grid;place-items:center;font-size:20px;font-weight:700;flex:none}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;align-items:center}.th .chips{margin:0}
.acts{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.acts .btn,.ops .btn{display:inline-flex;align-items:center;gap:8px;text-decoration:none}
.askb{display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;background:var(--warnb);color:var(--warn);border-radius:var(--r-lg);padding:16px 20px;margin:0 0 16px}
.askb>div:first-child{flex:1 1 420px;min-width:0}.askb p{margin:0}.askb .t{font-weight:700}
.cols{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}.mainc{flex:999 1 560px;min-width:0;display:grid;grid-template-columns:minmax(0,1fr);gap:16px}.sidec{flex:1 1 320px;min-width:0;display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
.mainc>section,.sidec>section{margin:0}
.th{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px}.th h2{margin:0}
.tcard{display:grid;gap:18px}
.steps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.steps li{display:grid;gap:4px;font-size:12px;color:var(--mute)}.steps b{font-size:13px;font-weight:600}.steps .bar{height:6px;border-radius:3px;background:var(--line)}
.steps li.on{color:var(--fg)}.steps li.on .bar{background:var(--pri)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px 24px;padding:16px;background:var(--muteb);border-radius:var(--r)}
.fk{font-size:13px;color:var(--mute);margin:0}.fv{font-size:15px;margin:2px 0 0}.fv.strong{font-weight:600}.wrapw{overflow-wrap:anywhere}
.ops{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start}.ops form{margin:0}.ops .cancel{margin-left:auto}
summary.btn.danger{background:var(--card);color:var(--bad);border-color:var(--badb)}summary.btn.danger:hover{background:var(--badb)}
.hist,.fu{border-top:1px solid var(--line);padding-top:14px}.hist .list li,.sidec .list li{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:4px 0}
.lbl{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--mute);margin:0 0 6px}
.note_{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}.note_ h2{margin:0;font-size:16px}.w100{flex:1 1 100%}
.side{display:grid;gap:12px}.side h2{font-size:16px;margin:0}.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.span2{grid-column:1/-1}
summary.lnk{cursor:pointer;color:var(--pri);font-weight:600;font-size:14px;list-style:none}
.pill{font:inherit;font-size:13px;font-weight:600;padding:6px 12px;min-height:32px;border-radius:999px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.pill[aria-pressed=true]{background:var(--pri);border-color:var(--pri);color:var(--on)}
td.nw{white-space:nowrap}
@media (max-width:640px){.steps{grid-template-columns:repeat(2,minmax(0,1fr))}.ops .cancel{margin-left:0}.avatar{width:44px;height:44px;font-size:16px}}
"""


def page(title, body, head=""):
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta name=robots content=noindex><title>%s</title><style>%s</style>%s</head><body>%s"
            "<script>document.querySelectorAll('form[data-once]').forEach(f=>f.addEventListener('submit',()=>{const b=f.querySelector('button[type=submit]');"
            "if(b){b.disabled=true;b.textContent=b.dataset.busy||'Please wait...'}}))</script></body></html>") % (e(title), CSS, head, body)


NAV = [("./", "Overview"), ("bt", "BT dashboard"), ("unbooked", "Not booked"), ("send", "Send WhatsApp"), ("analytics", "Analytics"),
       ("legacy", "Legacy data"), None, ("skus", "SKUs"), ("settings", "Settings")]
DOWNLOADS = [("export.csv", "Export CSV (all tests)"), ("results.csv", "Results CSV"), ("legacy.csv", "Legacy sheet CSV")]


def frame(who, mode, body, m="", hold=""):
    badge = {"live": ("Live", "ok"), "test": ("Test mode", "warn"), "off": ("Sending off", "bad")}.get(mode, (mode, "mute"))
    em = who.get("email") or ""
    tabs = "".join("<span class=sep aria-hidden=true></span>" if x is None else "<a href='%s'>%s</a>" % x for x in NAV)
    dls = "".join("<a href='%s'>%s</a>" % x for x in DOWNLOADS)
    return page("Sova blood tests", "<header class=top><div class=hd><a class=brand href='./'><img src='logo.webp' alt='' onerror=\"this.replaceWith(Object.assign("
                "document.createElement('span'),{className:'mk',textContent:'S'}))\"><span><b>Sova</b><small>Program blood tests</small></span></a>"
                "<span class='chip %s' title='WhatsApp send mode'>%s</span><div class=who><span class=av aria-hidden=true>%s</span><span class=em>%s</span>"
                "<a class=out href='logout'>Sign out</a></div></div><div class=navrow><nav class=tabs aria-label=Main>%s</nav>"
                "<details class=dl><summary>Downloads</summary><div>%s</div></details></div></header><main>%s%s%s</main>"
                "<script>(function(){var p=location.pathname.replace(/^.*\\//,'')||'./';if(/^legacy/.test(p))p='legacy';if(p==='p'||p==='')p='./';"
                "document.querySelectorAll('nav.tabs a').forEach(function(a){if(a.getAttribute('href')===p)a.setAttribute('aria-current','page')});"
                "document.addEventListener('click',function(ev){document.querySelectorAll('details.dl[open]').forEach(function(d){if(!d.contains(ev.target))d.open=false})})})()</script>"
                % (badge[1], badge[0], e(em[:1] or "?"), e(em), tabs, dls, "<p class=banner role=status>%s</p>" % e(m) if m else "",
                   "<p class='banner warn' role=alert>WhatsApp on hold: template <b>%s</b> isn't in Briza yet, or Meta hasn't approved it. Nothing is sent "
                   "with it until then; sends resume by themselves. <a href='settings#templates'>Template text</a></p>" % e(hold.replace(",", ", ")) if hold else "", body))


def chip(st): l, c = STATUS.get(st, (st, "mute")); return "<span class='chip %s'>%s</span>" % (c, e(l))


def day(d): return dt.date.fromisoformat(str(d)[:10]).strftime("%d %b") if d else ""


def ago(t):
    if not t: return ""
    h = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
    return "%dh ago" % h if h < 48 else "%dd ago" % (h // 24)


def plink(t): return "p?id=" + urllib.parse.quote(t["program"]["program_id"])


def last_msg(t):
    ms = [r for r in t.get("logs", []) if r.get("test_no") == t["n"] and r["kind"] == "msg"]
    if not ms: return "<span class=m>-</span>"
    r = ms[-1]
    return "%s %s <span class='m small'>%s</span>%s" % (e(msg_name(r)), e(r["status"]), ago(r["at"]),
                                                       "<div class='m small'>Tapped “%s” %s</div>" % (e(t["tap"]["status"]), ago(t["tap"]["at"])) if t.get("tap") else "")


def flags(t):
    """Extra chips next to the status: failed send/booking, booked after the due date, an open customer request, a confirmed visit."""
    return "".join((" <span class='chip bad'>Failed</span>" if t["failed"] else "", " <span class='chip warn'>Late</span>" if t["late"] else "",
                    " <span class='chip warn'>%s</span>" % ASK[t["ask"]] if t["ask"] else "", " <span class='chip ok'>Confirmed</span>" if t["confirmed"] else ""))


def sheet_note(t):
    """A legacy sheet test in the overview: the sheet's status, where it was done, and an 'other platform' mark when that wasn't Redcliffe."""
    return ("<div class='m small'>%s%s%s</div>%s" % (e(t["sheet_st"]), " · " + e(t["platform"]) if t["platform"] else "", " · done " + day(t["done"]) if t["done"] else "",
                                                     " <span class='chip warn'>Other platform</span>" if t["other_platform"] else ""))


def pf_chip(pf, done=True):
    """Where a test was done: Redcliffe (ours), another lab / the customer's own lab (another platform), or not recorded."""
    if not pf: return "<span class=m>not recorded</span>"
    import legacy_bt as LB
    return e(pf) + (" <span class='chip warn'>Other platform</span>" if LB.other_platform(pf) and done else "")


def call_btn(t):
    return "<a class='btn ghost' href='tel:+%s'>Call</a>" % e(t["program"]["phone"]) if t["program"]["phone"] else ""


def act_form(a, t, label, back, ghost=False):
    return ("<form method=post action=act class=row style='display:inline'><input type=hidden name=a value=%s><input type=hidden name=pid value='%s'>"
            "<input type=hidden name=n value=%d><input type=hidden name=back value='%s'><button class='%s'>%s</button></form>"
            % (a, e(t["program"]["program_id"]), t["n"], e(back), "ghost" if ghost else "", e(label)))


def dashboard(rows, args, who, mode, hold=""):
    live = [t for t in rows if t["status"] not in ("closed", "sheet")] # legacy sheet records are history: never counted or queued
    soon = (dt.datetime.now(IST).date() + dt.timedelta(days=7)).isoformat()
    booked = [t for t in live if t["booking"]]
    kpi = [("Active programs", len({t["program"]["program_id"] for t in live if t["status"] not in ("report", "untracked")}), ""),
           ("Customer asked", sum(bool(t["ask"]) for t in live), "asked"),
           ("To book", sum(t["status"] in GROUP["todo"] for t in live), "todo"),
           ("Needs follow-up", sum(t["status"] == "followup" for t in live), "followup"),
           ("Booked, next 7 days", sum(t["status"] == "booked" and t["booking"]["collection_date"] <= soon for t in live), "booked"),
           ("Rescheduled", "%d <span class='m small'>(%d moves)</span>" % (sum(t["resched"] > 0 for t in live), sum(t["resched"] for t in live)), ""),
           ("Collected / report", sum(t["status"] in GROUP["done"] for t in live), "done"),
           ("Booked on time", "%d%%" % (100 * sum(not t["late"] for t in booked) / len(booked)) if booked else "-", ""),
           ("Failed sends / bookings", sum(bool(t["failed"]) for t in live), "issues")]
    tiles = "".join("<div class='card kpi'><a href='%s'><div class=n>%s</div><div class=l>%s</div></a></div>" % ("./?s=" + s if s else "./", n, e(l))
                    for l, n, s in kpi)

    def queue(title, items, why, empty, btn=None):
        btn = btn or (lambda t: act_form("send", t, "Re-send", "./", True) if t["status"] in GROUP["todo"] + ("missed", "cancelled") and t["program"]["phone"] else "")
        li = "".join("<li><span><a href='%s'>%s</a> <span class='m small'>Test %d · %s</span></span><span class=row>%s</span></li>"
                     % (plink(t), e(t["program"]["customer_name"] or t["program"]["order_name"]), t["n"], e(why(t)), btn(t))
                     for t in items[:8])
        more = "<p class='small m'>+%d more in the table below</p>" % (len(items) - 8) if len(items) > 8 else ""
        return "<section class=card aria-label='%s'><h2>%s <span class='chip mute'>%d</span></h2>%s%s</section>" % (
            e(title), e(title), len(items), "<ul class=list>%s</ul>" % li if items else "<p class='m small'>%s</p>" % e(empty), more)

    asked = sorted([t for t in live if t["ask"]], key=lambda t: t["ask_at"])
    fu = sorted([t for t in live if t["status"] == "followup"], key=lambda t: t["t1_at"])
    bad = [t for t in live if t["failed"]]
    chk = [t for t in live if t["status"] in GROUP["issues"]]
    queues = ("<div class='grid q'>%s%s%s%s</div>" % (
        queue("Customer asked", asked, lambda t: "%s · %s" % (ASK[t["ask"]], ago(t["ask_at"])), "No open requests from WhatsApp buttons.", call_btn),
        queue("Needs follow-up", fu, lambda t: "Template 1 sent %s, not booked" % ago(t["t1_at"]), "Nobody is waiting more than 48 hours."),
        queue("Failed", bad, lambda t: ("Template %s WhatsApp failed" % t["failed"]["note"][1] if t["failed"]["kind"] == "msg" else t["failed"].get("note") or "Failed")[:80],
              "No failed WhatsApps or bookings."),
        queue("Check / missed", chk, lambda t: STATUS[t["status"]][0], "Nothing to check.")))
    s, qq = args.get("s", ""), (args.get("q") or "").strip().lower()
    pick = {"asked": lambda t: t["ask"], "issues": lambda t: t["failed"] or t["status"] in GROUP["issues"],
            "other": lambda t: t.get("other_platform")}.get(s, lambda t: t["status"] in GROUP.get(s, (s,)))
    sel = [t for t in rows if (pick(t) if s else (t["status"] != "sheet" or qq)) # 'All' is this desk's tests; a search also finds the legacy sheet
           and (not qq or qq in " ".join(str(t["program"].get(k) or "") for k in ("customer_name", "phone", "order_name", "email", "sku")).lower())]
    order = ["followup", "check", "missed", "cancelled", "to_send", "awaiting", "booked", "upcoming", "collected", "report", "untracked", "handed", "closed", "sheet"]
    sel.sort(key=lambda t: (not t["ask"], not t["failed"], order.index(t["status"]), t["booking"]["collection_date"] if t["booking"] else str(t["opens"])))
    filt = "".join("<a href='./?s=%s'%s>%s</a>" % (k, " aria-current=page" if k == s else "", l) for k, l in FILTERS)
    search = ("<form class=row role=search style='margin-left:auto'><input type=hidden name=s value='%s'><label for=q class=small>Search</label>"
              "<input id=q name=q value='%s' placeholder='Name, phone, order'><button class=ghost>Find</button></form>" % (e(s), e(qq)))
    trs = "".join("<tr><td><a href='%s'>%s</a><div class='m small'>%s</div></td><td>%s<div class='m small'>%s</div></td><td>%s<div class='m small'>%s days</div></td>"
                  "<td>Test %d</td><td>%s - %s</td><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                  % (plink(t), e(t["program"]["customer_name"] or "-"), e(t["program"]["phone"] or "no phone"), e(t["program"]["order_name"]),
                     e(t["program"]["source"] or ""), e(t["program"]["sku"]), e(t["program"]["days"]), t["n"], day(t["opens"]), day(t["due"]),
                     chip(t["status"]), flags(t) if t["status"] != "sheet" else sheet_note(t),
                     "%s <span class='m small'>%s</span>" % (day(t["booking"]["collection_date"]), e(t["booking"]["slot"])) if t["booking"] else "<span class=m>-</span>",
                     "%d / 2" % t["resched"] if t["booking"] else "<span class=m>-</span>", last_msg(t)) for t in sel[:500])
    table = ("<div class=wrap><table><caption class=sr-only>Blood tests</caption><thead><tr><th>Customer</th><th>Order</th>"
             "<th>Program</th><th>Test</th><th>Window</th><th>Status</th><th>Booking</th><th>Reschedules</th><th>Last WhatsApp</th></tr></thead>"
             "<tbody>%s</tbody></table></div>" % (trs or "<tr><td colspan=9 class=m>No tests match.</td></tr>"))
    return frame(who, mode, "<div class=row style='margin-bottom:10px'><h1 class=m0>Overview</h1>%s</div><div class='grid k'>%s</div>%s<div class=filters>%s%s</div>%s"
                 % ("<a class=btn style='margin-left:auto' href='add-user'>+ Add user</a><a class='btn ghost' href='dates'>Upload dates (CSV)</a>"
                    if who.get("role") == "admin" else "", tiles, queues, filt, search, table),
                 args.get("m", ""), hold)


STEPS = ("WhatsApp sent", "Booked", "Sample collected", "Report")
LIVE_ST = ("to_send", "awaiting", "followup", "booked", "check", "collected", "report")


def _ini(name):
    w = [x for x in re.split(r"\s+", (name or "").strip()) if x[:1].isalpha()]
    return ((w[0][0] + (w[-1][0] if len(w) > 1 else "")) if w else "?").upper()


def _steps(t):
    """The test's progress as four steps: which are done, with a date where we have one."""
    st, b = t["status"], t["booking"]
    sent = t.get("t1_at")
    done = [bool(sent) or st in LIVE_ST[2:], bool(b) or st in ("booked", "check", "collected", "report"), st in ("collected", "report"),
            st == "report" or bool(t.get("report"))]
    when = [day(sent) if sent else "", day(b["collection_date"]) if b else "", "", ""]
    return "".join("<li class='%s'><span class=bar></span><b>%s</b><span>%s</span></li>" % ("on" if d else "", e(l), e(w or ("Done" if d else "-")))
                   for l, d, w in zip(STEPS, done, when))


def _jd(r):
    d = (r or {}).get("detail")
    if isinstance(d, dict): return d
    try: return json.loads(d or "{}") or {}
    except (TypeError, ValueError): return {}


def _tl_kind(r):
    k = r["kind"]
    return "msg" if k in ("msg", "msg_manual", "reply", "click", "verified") else "book" if k in ("booked", "rescheduled", "cancelled") else "rcl" if k == "rcl" else "other"


def program(p, rows, logs, links, who, m, max_r, mode, hold="", extra=None):
    x = extra or {}
    bought = p["purchased_at"].astimezone(IST).date()
    start = p.get("program_start") or bought
    ends = p.get("program_end") or start + dt.timedelta(days=int(p["days"] or 0))
    moved = next((r for r in reversed(logs) if r["kind"] in ("start", "end")), None)
    via = _jd(moved).get("via") if moved else None
    me = urllib.parse.quote(p["program_id"])
    # ---- the header: who, where they are, and how to reach them
    active = next((t for t in rows if t["status"] in LIVE_ST[:-1]), rows[-1] if rows else None)
    wa = "https://wa.me/" + p["phone"] if p["phone"] else ""
    bz = (x.get("briza") or "").replace("{phone}", p["phone"] or "").replace("{phone10}", (p["phone"] or "")[-10:]) if p["phone"] else ""
    contact = "".join([
        "<a class=btn href='%s' target=_blank rel=noopener>%s Open Briza chat</a>" % (e(bz), ICON_CHAT) if bz else
        ("<a class='btn ghost' href='settings#c_briza_chat_url'>%s Set up Briza chat link</a>" % ICON_CHAT if who.get("role") == "admin" and p["phone"] else ""),
        "<a class='btn ghost' href='tel:+%s'>%s Call</a>" % (e(p["phone"]), ICON_PHONE) if p["phone"] else "",
        "<a class='btn ghost' href='%s' target=_blank rel=noopener>%s WhatsApp</a>" % (e(wa), ICON_OUT) if wa else "<span class='chip bad'>No number</span>"])
    head = ("<section class='card phead'><div class=who_><span class=avatar aria-hidden=true>%s</span><div><h1>%s</h1><div class=chips>%s%s%s</div></div></div>"
            "<div class=acts>%s</div></section>"
            % (e(_ini(p["customer_name"])), e(p["customer_name"] or p["order_name"]),
               ("<span class='chip %s'>Test %d · %s</span>" % (STATUS.get(active["status"], ("", "mute"))[1], active["n"], e(STATUS.get(active["status"], (active["status"],))[0])))
               if active else "", "<span class='chip mute'>%s · %s days</span>" % (e(p["product"] or p["sku"]), e(p["days"])),
               "<span class='chip mute'>%s · %s</span>" % (e(p["order_name"]), e(p["source"] or "-")), contact))
    # ---- what the customer asked for, at the top
    asks = "".join("<section class='askb' role=status><div><p class=t>%s · Test %d</p><p>Tapped “%s” on WhatsApp %s. %s</p></div><div class=acts>%s"
                   "<a class='btn ghost' href='#fu%d'>Log a follow-up</a></div></section>"
                   % (ASK[t["ask"]], t["n"], e(t["tap"]["status"]), ago(t["ask_at"]), NEXT[t["ask"]],
                      "<a class=btn href='%s'>Reschedule for customer</a>" % e(links[t["n"]]) if t["status"] == "booked" and t["resched"] < max_r else "", t["n"])
                   for t in rows if t["ask"])
    # ---- one card per test
    cards = []
    for t in rows:
        b, n = t["booking"], t["n"]
        hist = "".join("<li><span>%s %s, %s</span><span class='m small'>%s · %s</span></li>" % (
            {"booked": "Booked", "rescheduled": "Rescheduled to", "cancelled": "Cancelled"}[r["kind"]], day(r["collection_date"]), e(r["slot"]), e(r["by_user"]),
            r["at"].astimezone(IST).strftime("%d %b %H:%M")) for r in logs if r.get("test_no") == n and r["kind"] in ("booked", "rescheduled", "cancelled"))
        send_lbl = "Re-send reminder (Template 2)" if t["status"] == "booked" else \
            "Send booking WhatsApp (personalised test)" if p.get("sku") == "CUSTOM" else "Send booking WhatsApp (Template 1)"
        tomorrow = (dt.datetime.now(IST).date() + dt.timedelta(days=1)).isoformat()
        can_send = p["phone"] and (t["bookable"] or t["status"] == "booked" and b["collection_date"] == tomorrow) # Template 2 says "tomorrow"
        hidden = "<input type=hidden name=pid value='%s'><input type=hidden name=n value=%d><input type=hidden name=back value='%s'>" % (e(p["program_id"]), n, e(plink(t)))
        ops = (["<a class=btn href='%s'>Book for customer</a>" % e(links[n])] if t["bookable"] else []) + (
            (["<a class=btn href='%s'>Reschedule</a>" % e(links[n])] if t["resched"] < max_r else ["<span class='m small'>Both reschedules used: cancel and book again.</span>"])
            if t["status"] == "booked" else []) + ([followup_form(t, plink(t)) if t["bookable"] else act_form("send", t, send_lbl, plink(t), True)] if can_send else []) + (
            [act_form("refresh", t, "Check Redcliffe status + report", plink(t), True)] if b else []) + (
            [act_form("next_stage", t, NEXT_STAGE[t["status"]], plink(t))] if p.get("source") == "test" and t["status"] in NEXT_STAGE
            and (t["status"] != "report" or n == 1 and len(rows) > 1 and rows[1]["status"] == "upcoming") else [])
        cancel = ("<details class=cancel><summary class='btn danger'>Cancel booking</summary><form method=post action=act class=stack style='margin-top:10px' "
                  "onsubmit=\"return confirm('Cancel Redcliffe booking %s? This can\\'t be undone.')\"><input type=hidden name=a value=cancel>%s"
                  "<label for=cr%d>Reason</label><select id=cr%d name=reason required><option value=''>Pick a reason</option>%s</select>"
                  "<button class=danger>Cancel booking in Redcliffe</button></form></details>"
                  % (e(b["booking_id"]), hidden, n, n, "".join("<option>%s</option>" % e(r) for r in CANCEL_REASONS))) if t["status"] in ("booked", "check") else ""
        rcl = (" · ".join(x_ for x_ in (e(t["rcl_status"] or ""), "ID %s" % e(b["booking_id"]) if b else "") if x_) or "-") + (" · <a href='%s' target=_blank rel=noopener>Report</a>" % e(t["report"]) if t["report"] else "") + (
            " · <a href='%s' target=_blank rel='noopener noreferrer'>Track phlebo</a>" % e(t["tracking"]) if t.get("tracking") and t["status"] in ("booked", "check") else "")
        cards.append(
            "<section class='card tcard' id=test%d><div class=th><h2>Test %d</h2><div class=chips>%s%s</div></div>%s"
            "<div class=facts><div><p class=fk>Collection</p><p class='fv strong'>%s</p></div><div><p class=fk>Window</p><p class=fv>%s – %s</p></div>"
            "<div><p class=fk>Redcliffe</p><p class=fv>%s</p></div><div><p class=fk>Reschedules</p><p class=fv>%d of %d used</p></div></div>%s"
            "<div class=ops>%s%s</div>%s%s"
            "<form method=post action=act class='stack fu'><input type=hidden name=a value=followup>%s<label for=fu%d class=lbl>Log a follow-up</label>%s"
            "<textarea id=fu%d name=note rows=2 class=full placeholder='Called, will book for Saturday morning'></textarea><div><button class=ghost>Save follow-up</button></div></form></section>"
            % (n, n, chip(t["status"]), flags(t), "<ol class=steps aria-label='Test %d progress'>%s</ol>" % (n, _steps(t)) if t["status"] in LIVE_ST else "",
               "%s · %s" % (day(b["collection_date"]), e(b["slot"])) if b else "-", day(t["opens"]), day(t["due"]), rcl, t["resched"], max_r,
               "<p class='banner bad m0'>%s</p>" % e("Template %s WhatsApp failed: see the timeline" % t["failed"]["note"][1] if t["failed"]["kind"] == "msg" else t["failed"].get("note"))
               if t["failed"] else "",
               "".join(ops), cancel, "<div class=hist><p class=lbl>Booking history</p><ul class=list aria-label='Booking history'>%s</ul></div>" % hist if hist else "",
               followup_list(logs, n), hidden, n, "<p class='m small m0'>Saving one also closes the customer's WhatsApp request.</p>" if t["ask"] else "", n))
    if x.get("t1_covered"):
        cards.insert(0, "<section class='card note_'><h2>Test 1</h2><span class='chip mute'>Not needed</span><p class='m small m0 w100'>Repeat buyer: their previous "
                        "90+ day plan's Test 2 was done (%s), so it is this plan's starting point and no Test 1 is booked (the same rule as the legacy sheet). "
                        "Test 2 runs as usual.</p></section>" % e(x["t1_covered"].replace("previous plan's Test 2 ", "").strip("()")))
    if x.get("legacy"):
        cards.insert(0, "<section class='card note_'><h2>Test 1</h2><span class='chip mute'>Test taken</span> <span class='chip warn'>Data expired</span>"
                        "<p class='m small m0 w100'>Bought before tracking started (%s), so Test 1 happened outside this desk. No Redcliffe data is available for it; "
                        "only Test 2 is tracked here.</p></section>" % e(day(x.get("live_from")) or "go-live"))
    # ---- side column: details, dates, linked users, results
    info = ("<section class='card side'><h2>Customer details</h2><div><p class=fk>Phone</p><p class=fv>%s</p></div><div><p class=fk>Email</p><p class='fv wrapw'>%s</p></div>"
            "<div><p class=fk>Address</p><p class=fv>%s, %s %s</p></div><div><p class=fk>Order</p><p class=fv>%s <span class='m small'>· %s</span></p></div>"
            "<div><p class=fk>Purchased</p><p class=fv>%s</p></div></section>"
            % ("<a href='tel:+%s'>+%s</a>" % (e(p["phone"]), e(p["phone"])) if p["phone"] else "<span class='chip bad'>No number</span>", e(p["email"] or "-"),
               e(p["address"]), e(p["city"]), e(p["pincode"]),
               e(p["order_name"]) + (" + refill " + e(", ".join("#" + m_.split("-")[0] for m_ in p["members"][1:])) if len(p.get("members") or []) > 1 else ""),
               e(p["source"] or "-"), p["purchased_at"].astimezone(IST).strftime("%d %b %Y, %H:%M")))
    src = ("<span class='chip ok'>From the legacy sheet</span>" if via == "sheet" else "<span class='chip info'>Set by %s</span>" % e(moved["by_user"])) if moved else \
        "<span class='chip mute'>Purchase date</span>"
    can_edit = not (p.get("sku") == "CUSTOM" or p.get("source") == "linked" or who.get("role") != "admin")
    dates = ("<section class='card side'><div class=th><h2>Program dates</h2>%s</div><div class=two><div><p class=fk>Start</p><p class='fv strong'>%s</p></div>"
             "<div><p class=fk>End</p><p class='fv strong'>%s</p></div></div><p class='m small m0'>Test 2's booking WhatsApp goes 20 days before the end (once; never twice). "
             "Test 1 stays the day after purchase. Dates come from the legacy sheet every hour; a date set here wins.</p>%s</section>"
             % (src, start.strftime("%d %b %Y"), ends.strftime("%d %b %Y"),
                ("<details><summary class=lnk>Change dates</summary><form method=post action=act class='two' style='margin-top:12px'><input type=hidden name=a value=start_date>"
                 "<input type=hidden name=pid value='%s'><input type=hidden name=back value='%s'><div class=stack><label class=small for=sd>Start</label>"
                 "<input id=sd type=date name=date value='%s' required></div><div class=stack><label class=small for=ed>End</label><input id=ed type=date name=end value='%s' required></div>"
                 "<div class=span2><button class=ghost>Update dates</button></div></form></details>"
                 % (e(p["program_id"]), e("p?id=" + me), start.isoformat(), ends.isoformat())) if can_edit else ""))
    side = [info, dates]
    lk = x.get("linked") or {}
    if lk.get("buyer") or lk.get("users") or lk.get("made_for") or (who.get("role") == "admin" and p.get("source") not in ("linked", "custom")):
        b_, mf = lk.get("buyer"), lk.get("made_for")
        parts = []
        if mf and p.get("source") == "custom":
            parts.append("<p class=m0><b>Personalised test for %s</b> <span class='m small'>(profile ID %s)</span></p><p class=m0><a class='btn ghost' href='p?id=%s'>"
                         "Open %s's profile</a></p>" % (e(mf.get("customer_name") or "-"), e(mf["program_id"]), e(urllib.parse.quote(mf["program_id"])),
                                                        e((mf.get("customer_name") or "their").split(" ")[0].title())))
        if b_:
            parts.append("<p class=m0><b>Linked user</b> · %s of <b>%s</b>, whose order %s is on Shopify.</p><div><p class=fk>Parent ID</p><p class=fv>%s</p></div>"
                         "<div><p class=fk>Parent's number</p><p class=fv>%s</p></div><p class=m0><a class='btn ghost' href='p?id=%s'>Open %s's profile</a></p>"
                         % (e((lk.get("relation") or "linked").title()), e(b_.get("customer_name") or "the buyer"), e(b_.get("order_name") or ""), e(b_["program_id"]),
                            e(b_.get("phone") or "-"), e(urllib.parse.quote(b_["program_id"])), e((b_.get("customer_name") or "buyer").split(" ")[0].title())))
            if p.get("source") == "linked":
                parts.append("<p class='small m m0'>No program blood tests come to a linked user automatically. Send them a personalised test below; it goes to "
                             "%s.</p>" % ("their own number" if p.get("phone") and p.get("phone") != b_.get("phone") else "the parent's number"))
            if lk.get("notes"): parts.append("<p class='small m m0'>%s</p>" % e(lk["notes"]))
        if lk.get("users"):
            parts.append("<p class=grp>Linked users%s</p><ul class=list>%s</ul>" % (
                " <span class='chip info'>blood tests moved to them</span>" if lk.get("moved") else "",
                "".join("<li><span><b>%s</b> <span class='m small'>%s · %s</span></span><a class='btn ghost' href='p?id=%s'>Open profile</a></li>"
                        % (e(x_.get("name")), e((x_.get("relation") or "").title()), e(x_.get("phone") or "buyer's number"), e(urllib.parse.quote(x_.get("pid") or "")))
                        for x_ in lk["users"])))
        if not parts: parts.append("<p class='m small m0'>Family members on this order get their own profile and tests.</p>")
        if who.get("role") == "admin" and p.get("source") not in ("linked", "custom"):
            parts.append("<p class=m0><a class='btn ghost' href='add-user?link=%s'>+ Add linked user</a></p>" % e(me))
        side.append("<section class='card side'><h2>%s</h2>%s</section>" % ("Linked user" if b_ else "Linked users", "".join(parts)))
    side.append(results_card(p, x.get("results") or [], rows, x.get("legacy")))
    if p.get("sku") != "CUSTOM": side.append(other_test_card(p, x))
    if x.get("sheet"): cards.append(sheet_history(x["sheet"], "From the legacy sheet (Program Clean · Blood Test)"))
    # ---- timeline, filterable
    tl = "".join("<tr data-k='%s'><td class=nw>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=m>%s</td></tr>" % (
        _tl_kind(r), r["at"].astimezone(IST).strftime("%d %b %H:%M"), "Test %s" % r["test_no"] if r.get("test_no") else "-", e(event(r)), e(r.get("by_user") or ""),
        e(detail(r))) for r in reversed(logs))
    flt = "".join("<button type=button class=pill data-f='%s' aria-pressed='%s'>%s</button>" % (k, "true" if k == "all" else "false", l)
                  for k, l in (("all", "All"), ("msg", "WhatsApp"), ("book", "Bookings"), ("rcl", "Redcliffe")))
    timeline = ("<section class='card tl' style='padding:0'><div class=th style='padding:16px 20px'><h2>Timeline</h2><div class=chips role=group aria-label='Filter the timeline'>%s</div></div>"
                "<div class=wrap style='border:0;border-radius:0;box-shadow:none'><table><thead><tr><th>When</th><th>Test</th><th>What</th><th>By</th><th>Detail</th></tr></thead>"
                "<tbody>%s</tbody></table></div></section>"
                "<script>document.querySelectorAll('.tl .pill').forEach(function(b){b.addEventListener('click',function(){var f=b.dataset.f;"
                "document.querySelectorAll('.tl .pill').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});"
                "document.querySelectorAll('.tl tbody tr[data-k]').forEach(function(r){r.hidden=!(f==='all'||r.dataset.k===f)})})})</script>"
                % (flt, tl or "<tr><td colspan=5 class=m>Nothing yet.</td></tr>"))
    body = ("<p class=m0><a class=back href='./'>%s Back to overview</a></p>%s%s<div class=cols><div class=mainc>%s%s</div><aside class=sidec>%s</aside></div>"
            % (ICON_BACK, head, asks, "".join(cards), timeline, "".join(side)))
    return frame(who, mode, body, m, hold)


def dates_page(who, mode, hold, rows, m="", last=""):
    """Upload dates: a CSV of order_id, start_date, end_date -> each program's dates; Test 2 follows the new end date."""
    tbl = ""
    if rows:
        tbl = ("<div class=wrap><table><thead><tr><th>Row</th><th>Order</th><th>Customer</th><th>Start</th><th>End</th><th>Result</th></tr></thead><tbody>%s</tbody>"
               "</table></div>" % "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                   r.get("row"), e(r.get("order")), e(r.get("customer") or ""), e(r.get("start") or ""), e(r.get("end") or ""),
                   ("<span class='chip ok'>%s</span>" if r["result"].startswith("Updated") else "<span class='chip mute'>%s</span>" if r["result"] == "No change"
                    else "<span class='chip warn'>%s</span>") % e(r["result"])) for r in rows))
    body = ("<p><a href='./'>Back to overview</a></p><div class=row><h1 class=m0>Update program dates</h1><a class='btn ghost' style='margin-left:auto' "
            "href='dates/sample.csv'>Download sample CSV</a></div><p class='m small'>Upload a CSV with three columns: <b>order_id</b> (as on Shopify, with or "
            "without #), <b>start_date</b> and <b>end_date</b> (YYYY-MM-DD; DD-MM-YYYY works too). Each order's program gets those dates, and both changes "
            "show on the customer's timeline. Test 1 doesn't move (the day after purchase). Test 2's booking WhatsApp goes 20 days before the new end date, "
            "with the same reminders; a customer never gets Test 2 twice: if it's booked or done it stays, and if its WhatsApp went before only the reminders go.</p>"
            "<form method=post action=dates enctype=multipart/form-data class='card row' data-once><label for=df class=sr-only>CSV file</label>"
            "<input id=df type=file name=file accept='.csv,text/csv' required><button data-busy='Uploading...'>Upload and update</button></form>%s%s"
            % ("<p class='small m'>Last upload: %s</p>" % e(last) if last else "", tbl))
    return frame(who, mode, body, m, hold)


def add_user(found, qq, chosen, who, mode, hold, relations, m=""):
    """Overview > Add user: a person linked to a buyer (whose order is on Shopify): name, relation, their own WhatsApp number (the messages go
    there; empty = the buyer's), and details. Step 1 finds the buyer; step 2 the form."""
    pick = chosen[0] if chosen else None
    search = ("<form class='row card' role=search><label for=bq class=sr-only>Find the buyer</label><input id=bq name=q value='%s' placeholder='Buyer name, phone or "
              "order no.' style='flex:1' required minlength=2><button>Find buyer</button></form>" % e(qq))
    res = ""
    if qq and not pick:
        res = ("<ul class=list>%s</ul>" % "".join(
            "<li><span><b>%s</b> <span class='m small'>%s · %s · %s</span></span><a class='btn ghost' href='add-user?link=%s'>Link to this buyer</a></li>"
            % (e(p.get("customer_name") or "-"), e(p.get("phone") or "no phone"), e(p.get("order_name")), e(p.get("product") or p.get("sku")),
               e(urllib.parse.quote(p["program_id"]))) for p in found) if found else "<p class='m small'>No buyer matches. Try the phone number or order no.</p>")
    form = ""
    if pick:
        rel = "".join("<option value=%s>%s</option>" % (r, r.title()) for r in relations)
        form = ("<form method=post action=act class='card stack adduser' data-once><input type=hidden name=a value=add_user><input type=hidden name=link value='%s'>"
                "<input type=hidden name=back value='add-user?link=%s'><p class='banner okb m0'>Linked to <b>%s</b> · %s · order %s · %s</p>"
                "<p class='m small m0'>Parent ID: <b>%s</b>. The parent's own blood tests stay as they are; this person gets a profile, and you send them "
                "personalised tests from it.</p>"
                "<div class=stack><label for=au_n>Customer name</label><input id=au_n name=name required minlength=2 maxlength=60></div>"
                "<div class=stack><label for=au_r>Relation to %s</label><select id=au_r name=relation required><option value=''>Pick one</option>%s</select>"
                "<input name=relation_other placeholder='If other: e.g. cousin' maxlength=30 aria-label='Other relation'></div>"
                "<div class=stack><label for=au_p>Their WhatsApp number</label><input id=au_p name=phone inputmode=tel placeholder='10 digits'>"
                "<span class='m small'>All blood-test WhatsApps go to this number. Leave it empty to use the buyer's number (%s).</span></div>"
                "<div class='grid k fill'><div class=stack><label for=au_a>Age</label><input id=au_a name=age type=number min=5 max=110></div>"
                "<div class=stack><label for=au_g>Gender</label><select id=au_g name=gender><option value=''>-</option><option value=female>Female</option>"
                "<option value=male>Male</option></select></div><div class=stack><label for=au_e>Email</label><input id=au_e name=email type=email></div></div>"
                "<div class=stack><label for=au_ad>Address</label><input id=au_ad name=address placeholder='Empty = the buyer's address'></div>"
                "<div class='grid k fill'><div class=stack><label for=au_c>City</label><input id=au_c name=city></div><div class=stack><label for=au_pc>Pincode</label>"
                "<input id=au_pc name=pincode inputmode=numeric maxlength=6></div></div>"
                "<div class=stack><label for=au_no>Other details</label><textarea id=au_no name=notes rows=2 maxlength=300></textarea></div>"
                "<button type=submit data-busy='Adding...'>Add user</button></form>"
                % (e(pick["program_id"]), e(urllib.parse.quote(pick["program_id"])), e(pick.get("customer_name") or "-"), e(pick.get("phone") or "no phone"),
                   e(pick.get("order_name")), e(pick.get("product") or pick.get("sku")), e(pick["program_id"]),
                   e((pick.get("customer_name") or "the buyer").split(" ")[0]), rel,
                   e(pick.get("phone") or "none")))
    body = ("<p><a href='./'>Back to overview</a></p><h1>Add user</h1><p class='m small'>Add a person linked to a buyer (e.g. the buyer's mother). Pick the buyer "
            "first. The buyer keeps their own blood tests; the new person gets a profile showing the parent ID, and you send them personalised tests from it.</p>"
            "%s%s%s" % ("" if pick else search, res, form + ("<p class=small><a href='add-user'>Pick a different buyer</a></p>" if pick else "")))
    return frame(who, mode, body, m, hold)


def followup_form(t, back):
    """The profile's dropdown of booking follow-ups: send the first booking message again, or a booking reminder, now."""
    first = "Personalised test message (book)" if t.get("custom") else "First message: book your test" if t["n"] == 1 else "First message: book your follow-up test"
    return ("<form method=post action=act class=row style='display:inline-flex'><input type=hidden name=a value=send><input type=hidden name=pid value='%s'>"
            "<input type=hidden name=n value=%d><input type=hidden name=back value='%s'><label class=sr-only for=fw%d>Follow-up message</label>"
            "<select id=fw%d name=which><option value=first>%s</option><option value=nudge>Booking reminder (as on day 3 / 5 / 7)</option></select>"
            "<button class=ghost>Send follow-up</button></form>" % (e(t["program"]["program_id"]), t["n"], e(back), t["n"], t["n"], e(first)))


def followup_list(logs, n):
    """Every booking follow-up this test got: when, which, how it went, and whether the customer opened the link or booked after it."""
    ms = [r for r in logs if r["kind"] == "msg" and r.get("test_no") == n and r.get("note") in ("t1", "custom", "nudge")]
    if not ms: return ""
    clicks = [r["at"] for r in logs if r["kind"] == "click" and r.get("test_no") == n]
    books = [r["at"] for r in logs if r["kind"] == "booked" and r.get("test_no") == n and r.get("by_user") == "customer"]
    li, k = [], 0
    for i, m in enumerate(ms):
        if m.get("note") == "nudge": k += 1
        nxt = ms[i + 1]["at"] if i + 1 < len(ms) else m["at"] + dt.timedelta(days=30)
        name = "First message" if m.get("note") != "nudge" else "Reminder %d" % k
        c, b = any(m["at"] < x <= nxt for x in clicks), any(m["at"] < x <= nxt for x in books)
        li.append("<li><span>%s <span class='m small'>%s · %s</span></span><span>%s%s</span></li>" % (
            e(name), m["at"].astimezone(IST).strftime("%d %b %H:%M"), e(m["status"]),
            "<span class='chip ok'>Opened link</span> " if c else "<span class='chip mute'>Not opened</span> ", "<span class='chip ok'>Booked</span>" if b else ""))
    return ("<details><summary class='btn ghost'>Booking follow-ups (%d)</summary><ul class=list style='margin-top:8px'>%s</ul></details>" % (len(ms), "".join(li)))


def results_card(p, results, rows, legacy=False):
    """Every saved report for this customer (Test 1, Test 2): when, the report link, and the biomarkers read from it, flagged high / low."""
    by = {}
    for r in results: by.setdefault(int(r.get("test_no") or 1), []).append(r)
    parts = []
    if legacy: parts.append("<p class=small><b>Test 1</b>: test taken before tracking · <span class='chip warn'>Data expired</span> (no Redcliffe data)</p>")
    for t in rows:
        rs = by.get(t["n"]) or []
        if not rs:
            parts.append("<p class='small m'><b>Test %d</b>: no report yet (%s).</p>" % (t["n"], e(STATUS.get(t["status"], (t["status"],))[0])))
            continue
        r = rs[0]
        try: bio = json.loads(r.get("biomarkers") or "[]")
        except ValueError: bio = []
        trs = "".join("<tr><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            e(b.get("name")), e(b.get("value")), e(b.get("unit")), e(b.get("range")),
            "<span class='chip bad'>High</span>" if b.get("flag") == "H" else "<span class='chip warn'>Low</span>" if b.get("flag") == "L" else "") for b in bio)
        when = r["at"].astimezone(IST).strftime("%d %b %Y") if isinstance(r.get("at"), dt.datetime) else str(r.get("at") or "")[:10]
        saved = (" · <a href='pdf?id=%s' target=_blank rel=noopener>Saved copy (PDF)</a>" % e(r.get("id"))) if r.get("has_pdf") or r.get("report_pdf") else \
            " · <span class='chip warn'>No saved copy</span>"
        parts.append("<div class=stack><p class=m0><b>Test %d</b> · report %s · %s%s · <a href='%s' target=_blank rel='noopener noreferrer'>Redcliffe link</a>%s</p>%s</div>" % (
            t["n"], e(when), e(r.get("status")), saved, e(r.get("report_url")), " <span class='m small'>(%d earlier)</span>" % (len(rs) - 1) if len(rs) > 1 else "",
            "<div class=wrap><table style='min-width:520px'><thead><tr><th>Biomarker</th><th>Result</th><th>Unit</th><th>Range</th><th></th></tr></thead><tbody>%s"
            "</tbody></table></div>" % trs if trs else "<p class='m small m0'>No biomarkers could be read from this report; its text is kept, and the link above opens it.</p>"))
    return ("<section class='card stack' style='margin-top:14px'><div class=row><h2 class=m0>Test results</h2><a class='btn ghost' style='margin-left:auto' "
            "href='results.csv?id=%s'>Download results CSV</a></div>%s</section>" % (e(urllib.parse.quote(p["program_id"])), "".join(parts)))


def other_test_card(p, x):
    """Book a different test for this customer (an upgraded or other package): it goes as its own personalised test with a WhatsApp."""
    sib = "".join("<li><span><a href='p?id=%s'>%s</a></span><span class='m small'>sent %s</span></li>" % (
        e(urllib.parse.quote(s["program_id"])), e(s.get("product") or "Personalised test"),
        e(s["purchased_at"].astimezone(IST).strftime("%d %b %Y") if isinstance(s.get("purchased_at"), dt.datetime) else str(s.get("purchased_at") or "")[:10]))
        for s in x.get("siblings") or [])
    return ("<section class='card stack' style='margin-top:14px' id=other><h2>Book a different test</h2><p class='small m m0'>Everyone gets the default package (%s). "
            "For an upgraded or different test, enter its Redcliffe package code(s) and send: the customer gets <b>Your personalised blood test is here</b> on "
            "WhatsApp with their own booking link (OTP, address check, slot), and Redcliffe books exactly these codes.</p>"
            "<form method=post action=act class='grid k fill' data-once onsubmit=\"return confirm('Send this personalised test to the customer on WhatsApp?')\">"
            "<input type=hidden name=a value=custom_test><input type=hidden name=pid value='%s'><input type=hidden name=back value='%s'>"
            "<div class=stack><label for=ct_codes>Package code(s)</label><input id=ct_codes name=codes required placeholder='e.g. PL70, CAMP123'></div>"
            "<div class=stack><label for=ct_name>Test name shown to the customer</label><input id=ct_name name=name maxlength=60 placeholder='Advanced Thyroid Panel'></div>"
            "<div class=row style='align-items:end'><button type=submit data-busy='Sending...'>Send personalised test</button></div></form>"
            "<p class='small m m0'>Find codes under Settings › Redcliffe › Find packages.</p>%s</section>"
            % (e(x.get("codes") or "not set"), e(p["program_id"]), e("p?id=" + urllib.parse.quote(p["program_id"])),
               "<p class=grp>Personalised tests sent</p><ul class=list>%s</ul>" % sib if sib else ""))


def detail(r):
    """The why behind a timeline row: Briza's error, Redcliffe's status, the note, or which details changed."""
    k = r["kind"]
    try: d = json.loads(r.get("detail") or "{}") or {}
    except ValueError: d = {}
    if k == "msg":
        err = (d.get("result") or {}).get("error") if isinstance(d.get("result"), dict) else None
        return (err.get("message") or err.get("code") if isinstance(err, dict) else str(err or "")) if r["status"] == "failed" else d.get("template", "")
    if k == "rcl": return (r.get("status") or "") + (" · report ready" if d.get("report_link") else "")
    if k == "profile": return "Address placed on the map" if r.get("by_user") == "geocode" else "Gave: " + ", ".join(sorted(x for x in d if x not in ("lat", "lng")))
    if k == "reply": return ASK.get(r.get("note"), "")
    return r.get("note") or ""


def event(r):
    k = r["kind"]
    if k == "msg": return "%s WhatsApp %s" % (msg_name(r), r["status"])
    if k in ("booked", "rescheduled"): return "%s for %s, %s (ID %s)" % ("Booked" if k == "booked" else "Rescheduled", day(r["collection_date"]), r.get("slot"), r.get("booking_id"))
    if k == "cancelled": return "Cancelled booking %s (%s, %s)" % (r.get("booking_id"), day(r["collection_date"]), r.get("slot"))
    if k == "reply": return "Customer tapped “%s”" % r.get("status")
    return {"rcl": "Redcliffe status", "followup": "Follow-up", "error": "Redcliffe error", "profile": "Customer details", "closed": "Program closed",
            "verified": "Customer verified the number (WhatsApp OTP)", "report_view": "Customer opened the report", "shift": "Moved on (test)",
            "start": "Program start date changed (Test 2 moves)", "end": "Program end date changed (Test 2 moves)", "click": "Customer opened the booking link", "linked": "Linked user added",
            "handed": "Blood tests moved to a linked user"}.get(k, k)


def store_app_form(k, v, c):
    """Settings: one store's own Shopify app (Dev Dashboard > the app > Settings > Client ID / Client secret)."""
    label, domain, saved, hook = v
    stamp = c.get("shop_app_saved" + ("" if k == "health" else "_" + k), "")
    return ("<form method=post action=act class=stack><input type=hidden name=a value=shop_app><input type=hidden name=store value=%s><input type=hidden name=back value=settings>"
            "<p class='small m0'><b>%s</b> app <span class=m>(%s)</span>: paste its Client ID and Client secret. Shopify checks them first; they go straight to "
            "Secret Manager and are never shown again. Access renews itself every 24 hours. Order webhooks arrive at <code>%s</code>.</p>"
            "<label for=sa_id_%s>Client ID</label><input id=sa_id_%s name=cid autocomplete=off required minlength=8>"
            "<label for=sa_cs_%s>Client secret</label><div class=row><input id=sa_cs_%s name=cs type=password autocomplete=off required minlength=8>"
            "<button class=ghost>Save</button></div><span class='small %s'>%s</span></form>"
            % (k, e(label), e(domain), e(hook), k, k, k, k, "m" if saved else "warn",
               e("Saved %s. Paste new ones to replace them." % stamp if saved and stamp else "Saved." if saved else "Not saved yet: the SovaX desk's connection is used.")))


def settings(c, skus, who, m, packages, templates, mode, tests, host, hold="", live=None, users=(), legacy=(), sync=None):
    def cfg_form(k, label, value, hint=""):
        return ("<form method=post action=act class=field><input type=hidden name=a value=cfg><input type=hidden name=k value=%s><input type=hidden name=back value=settings>"
                "<label for=c_%s>%s</label><div class=row><input id=c_%s name=v value='%s'><button class=ghost>Save</button></div>%s</form>"
                % (k, k, e(label), k, e(value), "<span class='m small'>%s</span>" % e(hint) if hint else ""))
    modes = "".join("<label class=radio><input type=radio name=v value=%s%s><span>%s</span></label>" % (v, " checked" if v == mode else "", l)
                    for v, l in (("off", "Off: nothing goes out"), ("test", "Test: only test numbers get real WhatsApps and bookings"), ("live", "Live: every customer")))
    pk = ""
    if isinstance(packages, list):
        pk = "<ul class=list>%s</ul>" % "".join("<li><span><b>%s</b> %s</span><span class='m small'>%s · Rs %s · %s</span></li>" % (
            e(x.get("code")), e(x.get("name")), e(x.get("fasting_time") or ""), e((x.get("package_center_prices") or {}).get("offer_price")), e(x.get("recommended_gender") or ""))
            for x in packages) or "<p class=m>No packages found.</p>"
    elif packages: pk = "<p class='banner bad'>%s</p>" % e(packages)
    live = live or {"env": "staging", "key": False, "stores": {}}

    def secret_form(k, label, saved):
        return ("<form method=post action=act class=field><input type=hidden name=a value=secret><input type=hidden name=k value=%s><input type=hidden name=back value=settings>"
                "<label for=s_%s>%s</label><div class=row><input id=s_%s name=v type=password autocomplete=off required minlength=8><button class=ghost>Save</button></div>"
                "<span class='small %s'>%s</span></form>" % (k, k, e(label), k, "m" if saved else "bad", e("Saved %s. Paste a new one to replace it." % c.get(k + "_saved", "")
                                                                                                          if saved else "Not saved yet.")))
    env = ("<form method=post action=act class=stack><input type=hidden name=a value=cfg><input type=hidden name=k value=rcl_env><input type=hidden name=back value=settings>"
           "<fieldset><legend>Redcliffe bookings go to</legend>%s</fieldset><button>Save</button></form>"
           % "".join("<label class=radio><input type=radio name=v value=%s%s><span>%s</span></label>" % (v, " checked" if v == live["env"] else "", l)
                     for v, l in (("staging", "Staging (test bookings, no phlebotomist visits)"), ("prod", "Production (real home collections)"))))
    golive = ("<section class='card stack' id=live><h2>Go live (production)</h2><p class='small m'>Now: <b>%s</b> · %s. Keys go straight to Google Secret Manager "
              "and are never shown again.</p>%s%s%s%s%s<p class='small m m0'>Then set Send mode to Live above. The hourly run and the 11:00 / 13:00 / 17:00 checks "
              "(job <b>bt-reports</b>) use these within 5 minutes.</p></section>"
              % ("Production" if live["env"] == "prod" else "Staging", e(host), secret_form("rcl_prod_key", "1. Redcliffe production API key", live["key"]),
                 cfg_form("prod_package_codes", "2. Production package code(s)", c.get("prod_package_codes", ""), "from Redcliffe's production catalogue"),
                 cfg_form("rcl_prod_host", "3. Production API host", c.get("rcl_prod_host", "") or "https://api.redcliffelabs.com"),
                 "<p class='small m0'>4. Shopify stores: %s</p>" % " · ".join(
                     "%s <span class='chip %s'>%s</span>" % (e(k), "ok" if v.startswith("connected") else "warn", e(v)) for k, v in live.get("stores", {}).items())
                 + "".join(store_app_form(k, v, c) for k, v in (live.get("apps") or {}).items())
                 + "<form method=post action=act class=row><input type=hidden name=a value=shop_hooks><input type=hidden name=back value=settings>"
                   "<button class=ghost>Connect order webhooks (both stores)</button><span class='small m'>Subscribes each store's app to orders/create and "
                   "orders/cancelled, so a program order (online, in-store or a completed draft order) lands here at once. Test 1's WhatsApp goes the "
                   "next day; the hourly run is the backup.</span></form>"
                 + ("<div class=field><label for=hook>6. Redcliffe webhooks</label><input id=hook readonly value='%s' onclick='this.select()'>"
                    "<span class='small m'>Give Redcliffe this address for every webhook type (booking created, phlebo assigned, journey started / ended, "
                    "pickup, sample sync, rescheduled, cancelled, resample, partial and consolidated report). Keep it private: it works like a key.</span></div>"
                    % e(live.get("hook", "")) if live.get("hook") else "")
                 + cfg_form("live_from", "5. Go-live date", c.get("live_from", ""), "orders from this date get the whole flow; earlier buyers only get "
                            "their Test 2 message, and only if it's still ahead. Set automatically when Send mode goes Live."), env))
    team = ("<section class='card stack' id=team><h2>Team logins</h2><p class='small m'>Shared with the SovaX desk. Admin: everything, including settings and "
            "logins. View: sees every page and CSV, changes nothing.</p><div class=wrap><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Change</th>"
            "</tr></thead><tbody>%s</tbody></table></div><details><summary class='btn ghost'>Add a login</summary><form method=post action=act class='stack' "
            "style='margin-top:10px'><input type=hidden name=a value=user_add><input type=hidden name=back value=settings><label for=ua_e>Work email</label>"
            "<input id=ua_e name=email type=email required placeholder='name@sova.health' autocomplete=off><label for=ua_r>Role</label><select id=ua_r name=role>"
            "<option value=view>View</option><option value=admin>Admin</option></select><label for=ua_p>Password (8+ characters)</label>"
            "<input id=ua_p name=password type=password minlength=8 required autocomplete=new-password><button>Create login</button></form></details></section>"
            % ("".join("<tr><td>%s</td><td>%s</td><td>%s</td><td><details><summary class='btn ghost'>Reset password</summary><form method=post action=act class=row "
                       "style='margin-top:8px'><input type=hidden name=a value=user_reset><input type=hidden name=back value=settings><input type=hidden name=email "
                       "value='%s'><input name=password type=password minlength=8 required autocomplete=new-password aria-label='New password for %s'>"
                       "<button class=ghost>Save</button></form></details>%s</td></tr>"
                       % (e(r["email"]), "Admin" if r["role"] == "admin" else "View", "<span class='chip ok'>Active</span>" if r["active"] else "<span class='chip mute'>Off</span>",
                          e(r["email"]), e(r["email"]), "<form method=post action=act style='display:inline'><input type=hidden name=a value=%s><input type=hidden "
                          "name=back value=settings><input type=hidden name=email value='%s'><button class='ghost'>%s</button></form>"
                          % ("user_off" if r["active"] else "user_on", e(r["email"]), "Switch off" if r["active"] else "Switch on")) for r in users)
               or "<tr><td colspan=4 class=m>Only the setup logins so far.</td></tr>"))
    progs = sorted((r for r in skus.values() if int(r.get("days") or 0) >= 90), key=lambda r: (r.get("note") or "", r["sku"]))
    sk = "".join("<li><span><b>%s</b> %s</span><span class='m small'>%s · %d months</span></li>" % (e(r["sku"]), e(r.get("name")), e(r.get("note")), int(r["days"]) // 30)
                 for r in progs)
    body = ("<h1>Settings</h1><div class='grid q'><section class='card stack'><h2>Sending</h2><form method=post action=act class=stack><input type=hidden name=a value=cfg>"
            "<input type=hidden name=k value=send_mode><input type=hidden name=back value=settings><fieldset><legend>Send mode</legend>%s</fieldset><button>Save send mode</button></form>%s%s%s</section>"
            "<section class='card stack'><h2>Redcliffe</h2><p class='small m'>API: %s</p>%s<form class=field><label for=pks>Find packages</label><div class=row>"
            "<input id=pks name=packages placeholder='e.g. lipid, thyroid, full body'><button class=ghost>Search</button></div></form>%s</section>"
            "<section class='card stack' id=contact><h2>Customer support contact</h2><p class='small m'>Shown to customers on the booking page when Redcliffe can't collect "
            "at their address, or when they've used all their reschedules.</p>%s%s</section>"
            "%s<section class='card stack' id=templates><h2>WhatsApp templates (Briza)</h2>%s<details><summary>Template text (as submitted to Meta in Briza)</summary>%s"
            "<p class='small m'>Every button is a link with the test's own code: /b/ books or reschedules (after the WhatsApp number and an OTP), /c/ confirms. "
            "Links post nothing into the chat, so Briza's AI bot doesn't answer them.</p></details></section></div>"
            "<section class='card stack'><h2>Programs that get blood tests</h2><p class='small m'>Automatic, from every SKU of both stores (sova.in and sova.health), "
            "checked every hour: any program of 3 months or more. Test 1 the day after the order, Test 2 20 days before the program ends. "
            "<a href='skus'>Map SKUs (set a program's length)</a>.</p><p class='small m0'><b>Update Test 2 dates:</b> upload a CSV of order_id, start_date and "
            "end_date to set each program's dates. Test 1 stays as it is; Test 2 and its reminders follow the new end date, once per customer. "
            "<a class='btn ghost' href='dates'>Upload dates (CSV)</a> <a class='btn ghost' href='dates/sample.csv'>Download sample CSV</a></p><details><summary class='btn ghost'>"
            "Show the %s</summary><ul class=list style='margin-top:10px'>%s</ul></details><div class=row><form method=post action=act><input type=hidden name=a value=sync_skus>"
            "<input type=hidden name=back value=settings><button class=ghost>Check both stores now</button></form><form method=post action=act><input type=hidden name=a value=sync_orders>"
            "<input type=hidden name=back value=settings><button class=ghost>Get new orders now</button></form></div></section>"
            "<section class='card stack' style='margin-top:14px'><h2>Test profiles</h2><p class='small m'>Runs the real flow on your own number (it becomes a test number). "
            "On a test profile, <b>Move to next stage</b> plays Redcliffe's part: sample collected, report ready, then Test 2 opens.</p><form method=post action=act class='grid k fill'><input type=hidden name=a value=test_program>"
            "<input type=hidden name=back value=settings>%s<div class=row style='align-items:end'><button>Create</button></div></form></section>"
            % (modes, cfg_form("test_numbers", "Test numbers", ", ".join(tests), "comma separated"),
               cfg_form("quiet_minutes", "Hold automatic WhatsApps while the customer is chatting with Sova (minutes)", c.get("quiet_minutes", "") or "30",
                        "they go out once the customer has been quiet this long; 0 = never hold"),
               cfg_form("report_link_days", "Report link works for (days)", c.get("report_link_days", "") or "30", "shown in the report message")
               + cfg_form("t1_delay_days", "Test 1 booking WhatsApp goes this many days after the order", c.get("t1_delay_days", "") or "1",
                          "1 = the day after the order. Not booked? Booking reminders go 2, 4 and 6 days later (order day 3, 5 and 7)."), e(host),
               cfg_form("package_codes", "Package code(s)", c.get("package_codes", ""), "booked for each test"), pk,
               cfg_form("contact_phone", "Phone / WhatsApp number", c.get("contact_phone", ""), "10 digits; customers get Call and WhatsApp links"),
               cfg_form("contact_email", "Email", c.get("contact_email", ""), "optional") + cfg_form("briza_chat_url", "Briza chat link",
                        c.get("briza_chat_url", ""), "the web address of one customer's chat in Briza, with {phone} where their number goes "
                        "(91XXXXXXXXXX; {phone10} for 10 digits). Adds Open Briza chat on every customer page."), golive,
               "".join(cfg_form(k, TEMPLATE_TEXT[k][0], v) for k, v in templates.items()),
               "".join("<p class=small><b>%s</b> (%s)<br>%s</p>" % (e(l), e(c), e(x).replace("\n", "<br>")) for l, c, x in TEMPLATE_TEXT.values()),
               "%d program%s" % (len(progs), "" if len(progs) == 1 else "s"), sk or "<li class=m>None yet: SKUs load at the next hourly run.</li>",
               "".join("<div class=stack><label for=tp_%s>%s</label><input id=tp_%s name=%s %s></div>" % (k, l, k, k, a) for k, l, a in (
                   ("name", "Name", "value='Test Customer'"), ("phone", "WhatsApp number", "inputmode=tel required"), ("address", "Address", "required"),
                   ("city", "City", ""), ("pincode", "Pincode", "inputmode=numeric maxlength=6 required"), ("days", "Program days", "type=number value=120"),
                   ("ago", "Started days ago", "type=number value=0"))))) + team + legacy_section(legacy, sync)
    return frame(who, mode, body, m, hold)


def login_page(msg, nxt, email):
    return page("Sign in · Sova blood tests", "<main style='max-width:380px;margin:10vh auto'><div class='card stack'><img src='logo.webp' alt='Sova' class=logo "
                "onerror=\"this.remove()\"><h1>Program blood tests</h1><p class='m small'>Use your SovaX desk login.</p>%s<form method=post class=stack>"
                "<input type=hidden name=next value='%s'><div class=stack><label for=em>Email or username</label><input id=em name=email autocomplete=username value='%s' required class=full></div>"
                "<div class=stack><label for=pw>Password</label><input id=pw type=password name=password autocomplete=current-password required class=full></div>"
                "<button>Sign in</button></form></div></main>" % ("<p class='banner bad' role=alert>%s</p>" % e(msg) if msg else "", e(nxt), e(email)))


def verify(step, msg, hint):
    """Before booking or rescheduling: a 6-digit code goes on WhatsApp to the number on the order (no other number), then it's entered here."""
    if step == "code":
        form = ("<form method=post data-once class='card stack'><input type=hidden name=act value=check><div class=stack><label for=code>6-digit code</label>"
                "<input id=code name=code type=text inputmode=numeric autocomplete=one-time-code pattern='[0-9]{6}' maxlength=6 required class=full></div>"
                "<button type=submit data-busy='Checking...'>%s</button><p class='m small m0'>Sent on WhatsApp to %s. It expires in 10 minutes.</p></form>"
                "<form method=post data-once><input type=hidden name=act value=send><button class=ghost type=submit data-busy='Sending...'>Send a new code</button></form>"
                % ("Verify", e(hint)))
    else:
        form = ("<form method=post data-once class='card stack'><input type=hidden name=act value=send><p class=m0>We'll send a 6-digit code on WhatsApp to "
                "your registered number <b>%s</b>, the one you ordered your Sova program with.</p><button type=submit data-busy='Sending...'>Send code on WhatsApp</button>"
                "</form>" % e(hint))
    head, why = "Verify it's you", "To keep your booking private, we confirm it's you before you book or change your blood test."
    return page(head + " · Sova", "<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1>"
                "<p class=m>%s</p>%s%s</main>" % (head, why, "<p class=banner role=status>%s</p>" % e(msg) if msg else "", form))


def report(name, when, till):
    """The report link (no code): open the report in the browser, or save it to the phone."""
    return page("Your blood test report · Sova", "<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>Your blood test "
                "report</h1><section class='card stack'><p class=m0>Hi %s, your report from Redcliffe Labs%s is ready.</p><a class=btn href='?file=open'>Open report</a>"
                "<a class='btn ghost' href='?file=download' download='Sova blood test report.pdf'>Download</a><p class='m small m0'>This link works until %s. "
                "For your privacy, please don't forward it.</p></section></main>" % (e(name), " (sample collected %s)" % e(when) if when else "", e(till)))


def message_page(title, text, ok=False):
    return page(title, "<main class=book><div class='card stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1><p%s>%s</p></div></main>"
                % (e(title), " class='banner okb'" if ok else "", e(text)))


def contact_line(c):
    """Sova's support contact from Settings as links, for when the page can't help (e.g. an address Redcliffe doesn't cover)."""
    phone, email = (c or {}).get("phone", "").strip(), (c or {}).get("email", "").strip()
    digits = re.sub(r"\D", "", phone)
    wa = digits if len(digits) > 10 else "91" + digits
    bits = (["<a href='tel:+%s'>Call %s</a>" % (e(wa), e(phone)), "<a href='https://wa.me/%s' target=_blank rel=noopener>WhatsApp us</a>" % e(wa)] if digits else []) + (
        ["<a href='mailto:%s'>%s</a>" % (e(email), e(email))] if email else [])
    return " · ".join(bits) or "Message Sova on WhatsApp."


LOCATE_JS = """<script>(function(){var b=document.getElementById('loc');if(!b)return;b.onclick=function(){var m=document.getElementById('locmsg');
if(!navigator.geolocation){m.textContent="This browser can't share your location. Please type your address.";return}
m.textContent='Finding your location...';navigator.geolocation.getCurrentPosition(function(p){var la=p.coords.latitude,ln=p.coords.longitude;
if(window.sovaGo){sovaGo(la,ln);m.textContent='Found you (within '+Math.round(p.coords.accuracy)+' m). Move the map if the pin isn\\'t exactly on your home.'}
else{document.getElementById('lat').value=la.toFixed(6);document.getElementById('lng').value=ln.toFixed(6);document.getElementById('how').value='gps';
m.textContent='Location added (within '+Math.round(p.coords.accuracy)+' m). Check the address below, then save.'}},
function(){m.textContent="We couldn't get your location. Allow location access for this page, or search for your area."},{enableHighAccuracy:true,timeout:15000,maximumAge:0})}})()</script>"""

# Google map in a full-screen sheet, opened only by "Use my current location" (staff: "Set location on map"): the pin stays in the
# middle, the customer drags the map under it, Confirm fills the address. Maps loads on first open only (no map, no cost, easy scrolling).
MAP_JS = """<script>var SOVA=%s;(function(){const $=i=>document.getElementById(i),S=SOVA.start,IN={lat:22.5,lng:79},LBL=$('loc').textContent;
let map,gc,lib,cur=null,how='gps';const strip=a=>a.replace(/^[23456789CFGHJMPQRVWX]{4,8}\\+[23456789CFGHJMPQRVWX]{2,3},?\\s*/,'');
function load(){if(!lib)lib=new Promise((ok,no)=>{window.sovaMapsReady=ok;const s=document.createElement('script');s.async=true;s.onerror=no;
s.src='https://maps.googleapis.com/maps/api/js?key='+encodeURIComponent(SOVA.key)+'&loading=async&callback=sovaMapsReady&region=IN&language=en';
document.head.appendChild(s)}).then(()=>Promise.all([google.maps.importLibrary('maps'),google.maps.importLibrary('geocoding')])).then(([m,g])=>{window.SovaMap=m.Map;gc=new g.Geocoder()});return lib}
function read(){const c=map.getCenter();if(map.getZoom()<12){cur=null;$('confirm').disabled=true;$('picked').textContent='Zoom in and place the pin on your home.';return}
if(cur&&Math.abs(cur.lat-c.lat())<1e-6&&Math.abs(cur.lng-c.lng())<1e-6)return;
const me={lat:c.lat(),lng:c.lng(),r:null};cur=me;$('confirm').disabled=false;$('picked').textContent='Finding the address...';
gc.geocode({location:c}).then(({results})=>{if(cur!==me)return;const r=results.find(x=>!x.types.includes('plus_code'))||results[0];me.r=r||null;
$('picked').textContent=r?strip(r.formatted_address):'No street address here. You can still confirm and type it.'}).catch(()=>{if(cur===me)$('picked').textContent="Couldn't read the address here. You can still confirm and type it."})}
function show(c,z){$('sheet').hidden=false;document.body.style.overflow='hidden';$('confirm').disabled=true;$('sheet-h').focus();
return load().then(()=>{if(!map){map=new SovaMap($('map'),{center:c,zoom:z,disableDefaultUI:true,zoomControl:true,gestureHandling:'greedy',clickableIcons:false});
map.addListener('dragstart',()=>{how='pin'});map.addListener('idle',read)}else{map.setCenter(c);map.setZoom(z)}}).catch(()=>{$('picked').textContent="The map couldn't load. Please type your address."})}
function hide(){$('sheet').hidden=true;document.body.style.overflow='';$('loc').focus()}
function rest(){$('loc').disabled=false;$('loc').textContent=LBL}
function noGps(msg){how='pin';if(S.known)show({lat:S.lat,lng:S.lng},17);else show(IN,5).then(()=>S.q&&gc.geocode({address:S.q,componentRestrictions:{country:'IN'}})).then(x=>{
if(x&&x.results[0]){how='address';map.setCenter(x.results[0].geometry.location);map.setZoom(17)}}).catch(()=>{});$('hint').textContent=msg||'Drag the map so the pin sits exactly on your home.'}
function locHelp(code){const d=$('lochelp'),ios=/iPhone|iPad|iPod/.test(navigator.userAgent),blocked=code===1;
$('lochelp-t').textContent=blocked?'Allow location access':'Turn on your location';
$('lochelp-m').innerHTML=blocked?(ios?'Location is blocked for this page. Open <b>Settings › Privacy &amp; Security › Location Services</b>, turn it on and set your browser to <b>While Using the App</b>. Then come back and tap <b>Try again</b>.'
:'Location is blocked for this page. Tap the <b>lock icon</b> next to the web address › <b>Permissions</b> › <b>Location</b> › <b>Allow</b>. Then tap <b>Try again</b>.')
:(ios?'We couldn’t get your location: your phone’s Location Services may be off. Open <b>Settings › Privacy &amp; Security › Location Services</b>, turn it on, then tap <b>Try again</b>.'
:'We couldn’t get your location: your phone’s location may be off. Swipe down from the top of the screen and turn on <b>Location</b>, then tap <b>Try again</b>.');
if(d.showModal){if(!d.open)d.showModal()}else d.setAttribute('open','');$('lochelp-retry').focus()}
function shut(){const d=$('lochelp');if(d.close)d.close();else d.removeAttribute('open')}
function locate(){$('loc').disabled=true;$('loc').textContent='Finding your location...';navigator.geolocation.getCurrentPosition(p=>{rest();how='gps';show({lat:p.coords.latitude,lng:p.coords.longitude},18)},
e=>{rest();locHelp(e&&e.code)},{enableHighAccuracy:true,timeout:15000,maximumAge:0})}
$('loc').onclick=()=>{if(SOVA.staff||!navigator.geolocation){noGps(SOVA.staff?'':"This browser can't share your location. Search for your area or drag the map.");return}
if(navigator.permissions&&navigator.permissions.query)navigator.permissions.query({name:'geolocation'}).then(s=>{if(s.state==='denied')locHelp(1);else locate()}).catch(locate);else locate()};
$('lochelp-retry').onclick=()=>{shut();locate()};
$('lochelp-map').onclick=()=>{shut();noGps('Search for your area or drag the map so the pin sits exactly on your home.')};
$('setloc-c').onclick=e=>{e.preventDefault();how='pin';show({lat:+$('lat').value,lng:+$('lng').value},18)};
$('sheet-x').onclick=hide;document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('sheet').hidden)hide()});
$('find').onclick=()=>{const v=$('q').value.trim();if(!v)return;load().then(()=>gc.geocode({address:v,componentRestrictions:{country:'IN'}})).then(({results})=>{
if(!results[0])throw 0;how='search';map.setCenter(results[0].geometry.location);map.setZoom(17)}).catch(()=>{$('picked').textContent="We couldn't find that. Try an area name or a pincode."})};
$('q').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('find').click()}};
$('confirm').onclick=()=>{if(!cur)return;const r=cur.r;$('lat').value=cur.lat.toFixed(6);$('lng').value=cur.lng.toFixed(6);$('how').value=how;
if(r){const g=t=>(r.address_components.find(a=>a.types.includes(t))||{}).long_name||'';
const area=['premise','street_number','route','neighborhood','sublocality_level_3','sublocality_level_2','sublocality_level_1'].map(g).filter((v,i,a)=>v&&a.indexOf(v)===i).join(', ');
$('ar').value=area||strip(r.formatted_address);$('ct').value=g('locality')||g('administrative_area_level_3')||g('administrative_area_level_2')||$('ct').value;
if(g('postal_code'))$('pc').value=g('postal_code')}$('setloc-t').textContent=r?strip(r.formatted_address):'Pin set on the map';$('setloc').hidden=false;hide();$('hs').focus()}})()</script>"""
PIN_SVG = ("<svg class=pin viewBox='0 0 24 34' aria-hidden=true><path d='M12 0C5.4 0 0 5.3 0 11.9 0 20.8 12 34 12 34s12-13.2 12-22.1C24 5.3 18.6 0 12 0z' "
           "fill='#0e7490'/><circle cx=12 cy=12 r=4.5 fill='#fff'/></svg>")


def booking(t, prof, mode, dates, d, slots, why, msg, max_r, staff="", step="slots", contact=None, q="", maps_key=""):
    """The customer's page. New booking: 1 address (+ Redcliffe coverage) -> 2 day -> 3 time. Booked: the current booking -> new day and time.
    staff (?ops=1 only) adds the ops bar; q keeps ops=1 on the links."""
    p, b = t["program"], t["booking"]
    bar = ("<p class='banner warn'>Sova team: booking for <b>%s</b> as %s · <a href='../p?id=%s'>Back to profile</a></p>"
           % (e(p["customer_name"] or "the customer"), e(staff), e(urllib.parse.quote(p["program_id"])))) if staff else ""
    head = "Reschedule your blood test" if mode == "resched" else "Book your home blood test"
    intro = ("Your personalised blood test (%s) · a Redcliffe Labs phlebotomist collects the sample at home." % e(p.get("product") or "from the Sova team")
             if p.get("sku") == "CUSTOM" else
             "%s of your Sova program · a Redcliffe Labs phlebotomist collects the sample at home." % ("First test" if t["n"] == 1 else "Follow-up test"))
    if mode == "done":
        txt = {"upcoming": "Booking for this test opens on %s. We'll send you a WhatsApp then." % t["opens"].strftime("%d %b"),
               "collected": "Your sample has been collected. Your report will follow on WhatsApp.", "report": "Your sample has been collected and your report is ready.",
               "check": "Your collection date has passed. If the phlebotomist did not come, please message Sova.",
               "closed": "This program's tests are closed. Please message Sova if this looks wrong."}.get(t["status"], "Please message Sova for help.")
        return message_page(head, txt)
    where = ", ".join(str(x) for x in (prof.get("address"), prof.get("landmark"), prof.get("city"), prof.get("pincode")) if x)
    when_of = lambda r: "<b>%s</b>, %s" % (dt.date.fromisoformat(r["collection_date"]).strftime("%A, %d %b"), e(r["slot"]))
    left = max_r - t["resched"]
    top = ""
    if mode == "resched":
        new = max((i for i, r in enumerate(t["history"]) if r["kind"] == "booked"), default=0)
        earlier = "".join("<dt>Earlier slot</dt><dd>%s <span class='m small'>(moved)</span></dd>" % when_of(r) for r in t["history"][new:-1])
        top = ("<section class='card stack'><h2>Your booked slot</h2><dl><dt>When</dt><dd>%s</dd>%s<dt>Booking ID</dt><dd>%s</dd><dt>Name</dt><dd>%s</dd>"
               "<dt>Address</dt><dd>%s</dd></dl></section><p class='banner %s' role=note>%s</p>"
               % (when_of(b), earlier, e(b["booking_id"]), e(p["customer_name"]), e(where), "warn" if left > 0 else "bad",
                  ("Sova allows only %d reschedules for each test. You have %s left%s." % (max_r, "1" if left == 1 else left,
                   ": after this one, the time can't be changed online" if left == 1 else "") if left > 0
                   else "You've already used all %d reschedules Sova allows for this test." % max_r)))
        if left <= 0:
            return page(head, "<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1>%s%s<section class='card stack'>"
                        "<p class=m0>If you can't make this time, please contact the Sova team: %s</p></section></main>" % (head, bar, top, contact_line(contact)))
    elif t["history"]: # booked before and cancelled: show what it was
        last = t["history"][-1]
        top = ("<section class='card stack'><h2>Your previous booking</h2><dl><dt>When</dt><dd>%s</dd><dt>Booking ID</dt><dd>%s</dd><dt>Status</dt><dd>%s</dd>"
               "<dt>Address</dt><dd>%s</dd></dl></section>" % (when_of(last), e(last["booking_id"]), e(STATUS.get(t["status"], (t["status"],))[0]), e(where)))
    if mode == "book" and step == "address":
        house = prof.get("house") if "house" in prof else prof.get("address") # first visit: the order's address line
        known = prof.get("lat") not in (None, "") and prof.get("lng") not in (None, "")
        start = {"known": known, "lat": float(prof["lat"]) if known else 0, "lng": float(prof["lng"]) if known else 0,
                 "q": ", ".join(str(x) for x in (prof.get("address"), prof.get("city"), prof.get("pincode")) if x)}
        mapbox = ("<p id=setloc class='banner okb m0' hidden><b>Location set:</b> <span id=setloc-t></span> · <a href=# id=setloc-c>Change</a></p>"
                  "<div id=sheet class=sheet hidden role=dialog aria-modal=true aria-labelledby=sheet-h><div class=sheet-in><div class=sheet-top><h2 id=sheet-h tabindex=-1>"
                  "Set your exact location</h2><button type=button class=ghost id=sheet-x>Close</button></div><div class=row><input id=q type=search "
                  "placeholder='Search area, street or pincode' aria-label='Search for your area' autocomplete=off><button type=button class=ghost id=find>Find"
                  "</button></div><p id=hint class='m small m0'>Drag the map so the pin sits exactly on your home.</p><div class=mapwrap><div id=map role=application "
                  "aria-label='Map. Drag it so the pin sits on your home.'></div>%s</div><p id=picked class='small m0' aria-live=polite></p>"
                  "<button type=button id=confirm disabled>Confirm location</button></div></div>"
                  "<dialog id=lochelp class=lochelp aria-labelledby=lochelp-t aria-describedby=lochelp-m><h2 id=lochelp-t>Turn on your location</h2>"
                  "<p id=lochelp-m class=m0></p><div class=stack style='margin-top:14px'><button type=button id=lochelp-retry>Try again</button>"
                  "<button type=button class=ghost id=lochelp-map>Pick on the map instead</button></div></dialog>" % PIN_SVG) if maps_key else ""
        addr = ("<section class='card stack'><h2>1. Your address</h2><p class='m small m0'>%s</p>%s%s"
                "<form method=post data-once class=stack><input type=hidden name=act value=address>"
                "<input type=hidden id=lat name=lat><input type=hidden id=lng name=lng><input type=hidden id=how name=how value=address>"
                "<div class=stack><label for=hs>Flat / house no. / floor / building</label><input id=hs type=text name=house value='%s' required></div>"
                "<div class=stack><label for=ar>Area / street / locality</label><input id=ar type=text name=area value='%s' autocomplete=street-address></div>"
                "<div class=stack><label for=lm>Landmark (optional)</label><input id=lm type=text name=landmark value='%s'></div>"
                "<div class=row><div class=stack style='flex:1'><label for=ct>City</label><input id=ct type=text name=city value='%s' autocomplete=address-level2></div>"
                "<div class=stack style='flex:1'><label for=pc>Pincode</label><input id=pc type=text name=pincode value='%s' inputmode=numeric pattern='[0-9]{6}' maxlength=6 "
                "required autocomplete=postal-code></div></div><button type=submit data-busy='Checking...'>Save and check availability</button></form></section>%s%s"
                % ("The phlebotomist comes to this address. Use your current location to set the exact spot on the map, then add your flat / house no."
                   if maps_key else "The phlebotomist comes here. Check it, or use your current location, then save to see if home collection is available.",
                   "<button type=button class=ghost id=loc>Set location on map</button>" if staff and maps_key else "" if staff else
                   "<button type=button class=ghost id=loc>Use my current location</button><p id=locmsg class='m small m0' aria-live=polite></p>",
                   mapbox, e(house), e(prof.get("area") or ""), e(prof.get("landmark")), e(prof.get("city")), e(prof.get("pincode")),
                   "" if staff or maps_key else LOCATE_JS,
                   MAP_JS % json.dumps({"start": start, "key": maps_key, "staff": bool(staff)}).replace("</", "<\\/") if maps_key else ""))
        return page(head + " · Sova", "<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1><p class=m>%s</p>%s%s%s</main>"
                    % (head, intro, bar + ("<p class=banner role=status>%s</p>" % e(msg) if msg else ""), top, addr))
    addr = ("<section class='card stack'><h2>1. Your address</h2><p class=m0>%s</p>%s<a href='?%sstep=address'>Change address</a></section>"
            % (e(where), "<p class='banner okb m0'>Good news: Redcliffe home collection is available at this address.</p>" if step == "slots" else "", q)) if mode == "book" else ""
    if step == "blocked":
        return page(head + " · Sova", "<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1>%s%s%s"
                    "<section class='card stack'><p class='banner bad m0'>Sorry, Redcliffe Labs can't collect samples at this address yet.</p>"
                    "<p class=m0>Please contact the Sova team and we'll help you get your test done: %s</p></section></main>"
                    % (head, bar + ("<p class=banner role=status>%s</p>" % e(msg) if msg else ""), top, addr, contact_line(contact)))
    n0 = 1 if mode == "book" else 0 # step numbers after the address step
    days = "".join("<a href='?%sd=%s'%s>%s</a>" % (q, x, " aria-current=date" if x == d else "", dt.date.fromisoformat(x).strftime("%a %d %b")) for x in dates)
    if not d: when = "<p class='m small'>Pick a day to see the free time slots.</p>"
    elif not slots:
        when = "<p class=banner>%s</p>" % ("%s Please try again in a minute." % e(why) if why.startswith("Could not reach") else
                                           "No free slots on %s. Please try another day. If no day works, contact the Sova team: %s"
                                           % (dt.date.fromisoformat(d).strftime("%A, %d %b"), contact_line(contact)))
    else:
        groups = {}
        for i, l in slots:
            m = re.match(r"\s*(\d{1,2}):\d{2}\s*([AP]M)", l, re.I)
            h = int(m.group(1)) % 12 + (12 if m.group(2).upper() == "PM" else 0) if m else 9
            groups.setdefault("Morning" if h < 12 else "Afternoon" if h < 17 else "Evening", []).append((i, l))
        when = "<fieldset><legend>%d. Pick a time</legend>%s</fieldset>" % (n0 + 2, "".join(
            "<p class=grp>%s</p><div class='opt slots'>%s</div>" % (g, "".join("<label><input type=radio name=slot value='%s|%s' required><span>%s</span></label>"
                                                                            % (e(i), e(l), e(short(l))) for i, l in groups[g]))
            for g in ("Morning", "Afternoon", "Evening") if g in groups))
    details = ("<div class=stack><label for=age>Age</label><input id=age type=number name=age min=5 max=110 inputmode=numeric required value='%s'></div>"
               "<fieldset><legend>Gender</legend><div class=opt>%s</div></fieldset>"
               % (e(prof.get("age") or ""), "".join("<label><input type=radio name=gender value=%s required%s><span>%s</span></label>" % (
                   v, " checked" if prof.get("gender") == v else "", l) for v, l in (("male", "Male"), ("female", "Female"))))) if mode == "book" else ""
    form = ("<form method=post data-once class='card stack'><input type=hidden name=act value=%s><input type=hidden name=d value='%s'>%s%s<button type=submit data-busy='%s'>%s</button>"
            "<p class='m small' style='margin:0'>12 hours of fasting is needed before the visit (water is fine), so a morning slot is easiest. You can reschedule up to %d times.</p></form>"
            % (mode, e(d), when, details, "Booking..." if mode == "book" else "Moving...", "Book collection" if mode == "book" else "Move my booking", max_r)) if d and slots else when
    body = ("<main class='book stack'><img src='/logo.webp' alt='Sova' class=logo onerror=\"this.remove()\"><h1>%s</h1><p class=m>%s</p>%s%s%s"
            "<section class='card stack'><h2>%d. Pick a %sday</h2><nav class=opt aria-label='Days'>%s</nav></section>%s</main>"
            % (head, intro, bar + ("<p class=banner role=status>%s</p>" % e(msg) if msg else ""), top, addr, n0 + 1, "new " if mode == "resched" else "", days, form))
    return page(head + " · Sova", body)


def short(l):
    """'07:00 AM - 07:30 AM' -> '07:00-07:30 AM' so two slots fit side by side on a phone."""
    m = re.match(r"\s*(\d{1,2}:\d{2})\s*([AP]M)\s*-\s*(\d{1,2}:\d{2})\s*([AP]M)", l, re.I)
    if not m: return l
    return "%s-%s %s" % (m.group(1), m.group(3), m.group(2)) if m.group(2).upper() == m.group(4).upper() else "%s %s-%s %s" % m.groups()


def unbooked(rows, args, who, mode, hold=""):
    """Everyone whose test can be booked but isn't: tick them (or all) and send the booking WhatsApp in one go."""
    todo = [t for t in rows if t["status"] in BOOKABLE_UI and t["program"]["phone"]]
    s, qq = args.get("s", ""), (args.get("q") or "").strip().lower()
    sel = [t for t in todo if (not s or t["status"] == s) and
           (not qq or qq in " ".join(str(t["program"].get(k) or "") for k in ("customer_name", "phone", "order_name", "email", "sku")).lower())]
    sel.sort(key=lambda t: (BOOKABLE_UI.index(t["status"]), str(t["opens"])))
    count = lambda st: sum(t["status"] == st for t in todo)
    filt = "<a href='unbooked'%s>All (%d)</a>" % (" aria-current=page" if not s else "", len(todo)) + "".join(
        "<a href='unbooked?s=%s'%s>%s (%d)</a>" % (st, " aria-current=page" if st == s else "", e(STATUS[st][0]), count(st)) for st in BOOKABLE_UI)
    trs = "".join("<tr><td><input type=checkbox name=t value='%s|%d' aria-label='Select %s'></td><td><a href='p?id=%s'>%s</a><div class='m small'>%s · %s</div></td>"
                  "<td>Test %d</td><td>%s</td><td>%s - %s</td><td>%s</td></tr>"
                  % (e(t["program"]["program_id"]), t["n"], e(t["program"]["customer_name"] or t["program"]["order_name"]), e(urllib.parse.quote(t["program"]["program_id"])),
                     e(t["program"]["customer_name"] or t["program"]["order_name"]), e(t["program"]["order_name"]), e((t["program"].get("source") or "").split(" ")[0]),
                     t["n"], chip(t["status"]), day(t["opens"]), day(t["due"]), ago(t["t1_at"]) if t["t1_at"] else "<span class=m>Not yet</span>")
                  for t in sel[:500])
    more = "<p class='small m'>Showing 500 of %d. Narrow it with a filter or search.</p>" % len(sel) if len(sel) > 500 else ""
    form = ("<form method=post action=act onsubmit=\"var n=this.querySelectorAll('input[name=t]:checked').length;if(!n){alert('Tick at least one customer.');"
            "return false}return confirm('Send the booking WhatsApp to '+n+' customer'+(n>1?'s':'')+'?')\"><input type=hidden name=a value=bulk_send>"
            "<input type=hidden name=back value='unbooked%s'><div class=row style='margin:10px 0'><button>Send booking WhatsApp to ticked</button>"
            "<span class='m small'>Template 1 (Test 1) or 1b (Test 2), each with its own booking link. Anyone messaged in the last 24 hours is skipped. "
            "Test mode: only test numbers get it, the rest are logged as planned.</span></div><div class=wrap><table><thead><tr><th><input type=checkbox "
            "aria-label='Select all shown' onclick=\"this.closest('form').querySelectorAll('input[name=t]').forEach(c=>c.checked=this.checked)\"></th>"
            "<th>Customer</th><th>Test</th><th>Status</th><th>Window</th><th>Last booking WhatsApp</th></tr></thead><tbody>%s</tbody></table></div></form>%s"
            % ("?s=" + e(s) if s else "", trs or "<tr><td colspan=6 class=m>Nobody here: every bookable test is booked.</td></tr>", more))
    search = ("<form class=row role=search style='margin-left:auto'><input type=hidden name=s value='%s'><label for=q class=small>Search</label>"
              "<input id=q name=q value='%s' placeholder='Name, phone, order'><button class=ghost>Find</button></form>" % (e(s), e(qq)))
    return frame(who, mode, "<h1>Not booked yet</h1><p class='m small'>Every customer whose test is open for booking but not booked: not messaged yet, "
                 "messaged and waiting, needing a follow-up, past their window, or whose booking was cancelled.</p><div class=filters>%s%s</div>%s"
                 % (filt, search, form), args.get("m", ""), hold)


BOOKABLE_UI = ("to_send", "awaiting", "followup", "missed", "cancelled") # untracked: their window closed before the program reached us


def analytics(a, who, mode, hold=""):
    """Insights for every stage: where each test is now, messages and how customers responded, bookings and reschedules, reports, and the
    11:00 / 13:00 / 17:00 checks."""
    pct = lambda x, y: "%d%%" % round(100 * x / y) if y else "-"
    start, end = a.get("start"), a.get("end")
    period = {7: "the last 7 days", 30: "the last 30 days", 90: "the last 90 days", 0: "all time"}.get(a["days"]) or \
        "%s to %s" % (start.strftime("%d %b %Y"), end.strftime("%d %b %Y"))
    pick = "".join("<a href='analytics?days=%d'%s>%s</a>" % (d, " aria-current=page" if d == a["days"] else "", l)
                   for d, l in ((7, "7 days"), (30, "30 days"), (90, "90 days"), (0, "All time")))
    pick += ("<form class=row action=analytics aria-label='Date range'><label for=af class=small>From</label><input id=af type=date name=from value='%s' required>"
             "<label for=at class=small>To</label><input id=at type=date name=to value='%s' required><button class=ghost>Apply</button></form>"
             % (start.isoformat(), end.isoformat()))
    rq = "from=%s&to=%s" % (start.isoformat(), end.isoformat())
    xl = lambda kind, key, label="CSV": "<a class=small href='analytics.csv?kind=%s&key=%s&%s' title='Download these customers'>%s</a>" % (
        kind, e(urllib.parse.quote(key)), rq, e(label))
    r, mv = a["resp"], a["moves"]
    t2 = r.get("t2", {})
    sent = lambda k: r.get(k, {}).get("sent", 0) + r.get(k, {}).get("planned", 0)
    kpi = [("Plans", a["programs"], " · ".join("%s %d" % (k, v) for k, v in sorted(a["stores"].items()))),
           ("Booked", mv["booked"]["customer"] + mv["booked"]["team"], "%d by customers" % mv["booked"]["customer"]),
           ("Reschedules", mv["rescheduled"]["customer"] + mv["rescheduled"]["team"], "%d by customers" % mv["rescheduled"]["customer"]),
           ("Reminder: confirmed Yes", pct(t2.get("yes", 0), t2.get("sent", 0)), "of %d reminders sent" % t2.get("sent", 0)),
           ("Replied to a message", pct(sum(k["any"] for k in r.values()), sum(k["sent"] for k in r.values())), "within 48 hours"),
           ("Reports viewed", a["views"]["tests"], "of %d report links" % sent("report"))]
    tiles = "".join("<div class='card kpi'><div class=n>%s</div><div class=l>%s</div><div class='m small'>%s</div></div>" % (e(n), e(l), e(x)) for l, n, x in kpi)
    cols = [s for s in a["order"] if a["stages"][1].get(s) or a["stages"][2].get(s)]
    stage_rows = "".join("<tr><td>%s</td>%s<td><b>%d</b> %s</td></tr>" % (lbl, "".join("<td><a href='./?s=%s'>%d</a> %s</td>" % (
        s, st.get(s, 0), xl("stage", "%d:%s" % (n, s), "↓") if st.get(s) else "") for s in cols), sum(st.values()), xl("stage", "%d:" % n, "↓"))
                         for n, lbl, st in ((1, "Test 1", a["stages"][1]), (2, "Test 2", a["stages"][2])))
    stages = ("<div class=wrap><table style='min-width:640px'><thead><tr><th>Now</th>%s<th>All</th></tr></thead><tbody>%s</tbody></table></div>"
              % ("".join("<th>%s</th>" % e(STATUS[s][0]) for s in cols), stage_rows)) if cols else "<p class='m small'>No tests yet.</p>"
    fn = a.get("funnel") or {}
    funnel = ("<div class=wrap><table style='min-width:720px'><thead><tr><th>Message</th><th>Sent</th><th>Opened the link</th><th>Booked</th><th>Click rate</th>"
              "<th>Booking rate</th><th>Export customers</th></tr></thead><tbody>%s</tbody></table></div>" % "".join(
                  "<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td><td><b>%s</b></td><td>%s</td></tr>" % (
                      e(lbl), fn[k]["sent"], fn[k]["clicked"], fn[k]["booked"], pct(fn[k]["clicked"], fn[k]["sent"]), pct(fn[k]["booked"], fn[k]["sent"]),
                      " · ".join(xl("funnel", "%s:%s" % (k, m_), l_) for m_, l_ in (("sent", "sent"), ("clicked", "opened"), ("booked", "booked"))))
                  for k, lbl in a.get("funnel_labels") or ()) if fn else "")
    msg_rows = "".join("<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td></tr>"
                       % (e(MSG.get(k, k)), v["sent"], v["planned"], v["failed"], v["held"], pct(v["acted"], v["sent"]), pct(v["wrote"], v["sent"]),
                          pct(v["any"], v["sent"]), " · ".join(xl("msg", "%s:%s" % (k, m_), l_) for m_, l_ in (("sent", "sent"), ("any", "responded"),
                                                                                                            ("none", "no response"), ("failed", "failed")))
                          ) for k, v in sorted(r.items(), key=lambda kv: list(MSG).index(kv[0]) if kv[0] in MSG else 99))
    msgs = ("<div class=wrap><table style='min-width:860px'><thead><tr><th>WhatsApp</th><th>Sent</th><th>Planned (test mode)</th><th>Failed</th><th>Held</th>"
            "<th>Opened or used the link</th><th>Wrote back on WhatsApp</th><th>Any response</th><th>Export customers</th></tr></thead><tbody>%s</tbody></table></div>"
            % msg_rows) if r else "<p class='m small'>No WhatsApps in this period.</p>"
    remind = ("<ul class=list><li><span>Reminders sent (the day before)</span><b>%d</b></li><li><span>Confirmed with Yes</span><b>%d (%s)</b></li>"
              "<li><span>Asked to reschedule or rescheduled</span><b>%d (%s)</b></li><li><span>No tap and no reply</span><b>%d</b></li></ul>"
              % (t2.get("sent", 0), t2.get("yes", 0), pct(t2.get("yes", 0), t2.get("sent", 0)), t2.get("resched", 0), pct(t2.get("resched", 0), t2.get("sent", 0)),
                 t2.get("sent", 0) - t2.get("any", 0))) + \
        "<p class='small m0'>Export: %s · %s · %s</p>" % (xl("msg", "t2:sent", "reminded"), xl("msg", "t2:any", "responded"), xl("msg", "t2:none", "no response"))
    used = "".join("<li><span>%s</span><b>%d</b></li>" % ("Not rescheduled" if i == 0 else "Rescheduled %d of %d" % (i, a["max_r"]), n) for i, n in a["used"].items())
    resched = ("<ul class=list><li><span>Booked</span><b>%d <span class='m small'>(%d by customers, %d by the team)</span></b></li>"
               "<li><span>Rescheduled</span><b>%d <span class='m small'>(%d by customers, %d by the team)</span></b></li>"
               "<li><span>Cancelled</span><b>%d</b></li></ul><p class='small m0'>Export: %s · %s · %s</p><p class='grp'>Booked tests now, by reschedules used</p><ul class=list>%s"
               "<li><span>Still booked with none left</span><b>%d</b></li></ul>"
               % (mv["booked"]["customer"] + mv["booked"]["team"], mv["booked"]["customer"], mv["booked"]["team"],
                  mv["rescheduled"]["customer"] + mv["rescheduled"]["team"], mv["rescheduled"]["customer"], mv["rescheduled"]["team"],
                  mv["cancelled"]["customer"] + mv["cancelled"]["team"], xl("move", "booked", "booked"), xl("move", "rescheduled", "rescheduled"),
                  xl("move", "cancelled", "cancelled"), used, a["at_limit"]))
    reports = ("<ul class=list><li><span>Ready now</span><b>%d</b></li><li><span>Report links sent</span><b>%d</b></li><li><span>Viewed (tests)</span><b>%d</b></li>"
               "<li><span>Opened · downloaded</span><b>%d · %d</b></li></ul><p class='small m0'>Export: %s · %s · %s · <a class=small href='results.csv'>all "
               "results + biomarkers</a></p>"
               % (a["stages"][1].get("report", 0) + a["stages"][2].get("report", 0), sent("report"), a["views"]["tests"], a["views"]["open"], a["views"]["download"],
                  xl("report", "ready", "ready"), xl("report", "sent", "links sent"), xl("report", "viewed", "viewed")))
    daily = "".join("<tr><td>%s</td>%s</tr>" % (d.strftime("%a %d %b"), "".join("<td>%s</td>" % (x or "<span class=m>0</span>") for x in xs)) for d, *xs in a["daily"])
    checks = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (t.astimezone(IST).strftime("%d %b, %H:%M"), c.get("bookings_checked", "-"), c.get("status_updates", "-"), c.get("found", "-"),
                        "%s sent · %s planned" % (c.get("sent", 0), c.get("planned", 0)), int(c.get("waiting", 0)) + int(c.get("deferred", 0)))
                     for t, c in a["checks"]) or "<tr><td colspan=6 class=m>No checks yet. They run at 11:00, 13:00 and 17:00 IST.</td></tr>"
    pick += "<a href='analytics.csv?kind=all&%s'>Export all tests</a>" % rq
    body = ("<div class=row style='margin-bottom:12px'><h1 class=m0>Analytics</h1><nav class=filters aria-label=Period style='margin-left:auto'>%s</nav></div>"
            "<p class='m small'>Stages are as of now; everything else covers %s. A response counts when the customer opens or uses the link, or writes "
            "to Sova on WhatsApp, within 48 hours of the message.</p><div class='grid k'>%s</div>"
            "<section class='card stack' style='margin-top:14px'><h2>Where every test is now</h2>%s</section>"
            "<section class='card stack' style='margin-top:14px'><h2>Booking messages: clicks and bookings</h2><p class='m small m0'>For the first booking message "
            "and each reminder: how many customers opened the booking link, and how many booked, before the next message went.</p>%s</section>"
            "<section class='card stack' style='margin-top:14px'><h2>WhatsApps and responses</h2>%s</section>"
            "<div class='grid q'><section class='card stack'><h2>Day-before reminder</h2>%s</section>"
            "<section class='card stack'><h2>Bookings and reschedules</h2>%s</section><section class='card stack'><h2>Reports</h2>%s</section></div>"
            "<section class='card stack'><h2>Day by day</h2><div class=wrap><table style='min-width:640px'><thead><tr><th>Day</th><th>Booked</th>"
            "<th>Rescheduled</th><th>Confirmed Yes</th><th>Samples collected</th><th>Report links sent</th><th>Reports viewed</th></tr></thead><tbody>%s</tbody>"
            "</table></div></section><section class='card stack' style='margin-top:14px'><h2>Status checks (11:00, 13:00, 17:00 IST)</h2><p class='m small m0'>"
            "Each check asks Redcliffe about every scheduled or rescheduled booking and for new reports, then sends the due WhatsApps.</p><div class=wrap>"
            "<table style='min-width:640px'><thead><tr><th>When</th><th>Bookings checked</th><th>Status changes</th><th>Reports found</th><th>WhatsApps</th>"
            "<th>Waiting</th></tr></thead><tbody>%s</tbody></table></div></section>"
            % (pick, period, tiles, stages, funnel, msgs, remind, resched, reports, daily, checks))
    return frame(who, mode, body, "", hold)


def skus(table, args, who, mode, hold, min_days, needs_review, manual):
    """Every SKU of both stores with its program length. Programs (min_days+) get blood tests; the team maps the rest here. A SKU the sync
    can't size but looks like a program is listed under Needs review, so nothing slips through."""
    rows = sorted(table.values(), key=lambda r: (r.get("note") or "", r["sku"]))
    groups = {"programs": [r for r in rows if int(r.get("days") or 0) >= min_days], "review": [r for r in rows if needs_review(r)],
              "short": [r for r in rows if 0 < int(r.get("days") or 0) < min_days], "other": [r for r in rows if int(r.get("days") or 0) == 0],
              "mapped": [r for r in rows if manual(r)], "": rows}
    s, qq = args.get("s", "review" if groups["review"] else "programs"), (args.get("q") or "").strip().lower()
    if s not in groups: s = ""
    sel = [r for r in groups[s] if not qq or qq in ("%s %s %s" % (r["sku"], r.get("name") or "", r.get("note") or "")).lower()]
    labels = (("review", "Needs review"), ("programs", "Programs (%d+ days)" % min_days), ("mapped", "Mapped by the team"), ("short", "Under %d days" % min_days),
              ("other", "Not programs"), ("", "All"))
    filt = "".join("<a href='skus?s=%s'%s>%s (%d)</a>" % (k, " aria-current=page" if k == s else "", e(l), len(groups[k])) for k, l in labels)
    search = ("<form class=row role=search style='margin-left:auto'><input type=hidden name=s value='%s'><label for=q class=small>Search</label>"
              "<input id=q name=q value='%s' placeholder='SKU or name'><button class=ghost>Find</button></form>" % (e(s), e(qq)))
    trs = "".join("<tr><td><b>%s</b></td><td>%s%s</td><td>%s</td><td>%s</td><td><form method=post action=act class=row><input type=hidden name=a value=sku_map>"
                  "<input type=hidden name=sku value='%s'><input type=hidden name=back value='skus?s=%s'><input name=days type=number min=0 max=730 value='%s' "
                  "aria-label='Days for %s' style='width:90px'><button class=ghost>Save</button></form></td></tr>"
                  % (e(r["sku"]), e(r.get("name") or "-"), " <span class='chip warn'>Needs review</span>" if needs_review(r) else "", e(r.get("note") or ""),
                     "<span class='chip ok'>Gets blood tests</span>" if int(r.get("days") or 0) >= min_days else "<span class='chip mute'>No</span>",
                     e(r["sku"]), e(s), int(r.get("days") or 0), e(r["sku"])) for r in sel[:600])
    body = ("<h1>SKUs</h1><p class='m small'>Every SKU sold on sova.in and sova.health (online, in-store and draft orders), checked every hour. Programs of %d days "
            "or more get blood tests: Test 1 the day after the order, Test 2 20 days before the program ends. The sync sets the length when the SKU or name shows it "
            "(PR-WGL-120, '4 Months'); set it here when it doesn't. A length you save is never overwritten, and everyone who bought that SKU is picked up "
            "at once (orders from the go-live date get the whole flow; earlier ones only their Test 2). 0 = not a program.</p>"
            "<div class=filters>%s%s</div><div class=wrap><table><thead><tr><th>SKU</th><th>Name</th><th>Store</th><th>Blood tests</th><th>Program days</th></tr></thead>"
            "<tbody>%s</tbody></table></div>%s<form method=post action=act style='margin-top:12px'><input type=hidden name=a value=sync_skus>"
            "<input type=hidden name=back value=skus><button class=ghost>Check both stores for new SKUs now</button></form>"
            % (min_days, filt, search, trs or "<tr><td colspan=5 class=m>No SKUs here.</td></tr>",
               "<p class='small m'>Showing 600 of %d. Search to narrow it.</p>" % len(sel) if len(sel) > 600 else ""))
    return frame(who, mode, body, args.get("m", ""), hold)


def csv_rows(rows, cols):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v # stays text in Sheets / Excel, never a formula
    for r in rows: w.writerow([safe("" if r.get(c) is None else r.get(c)) for c in cols])
    return out.getvalue()


def export_csv(rows):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["program_id", "order", "source", "customer", "phone", "sku", "days", "purchased", "test", "opens", "due", "status", "failed", "booking_id",
                "collection_date", "slot", "reschedules", "late", "redcliffe_status", "report", "last_tap", "customer_asked", "confirmed",
                "sheet_status", "lab_platform", "other_platform", "done_on"])
    safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v # stays text in Sheets / Excel, never a formula
    for t in rows:
        p, b = t["program"], t["booking"] or {}
        w.writerow([safe(v) for v in [p["program_id"], p["order_name"], p["source"], p["customer_name"], p["phone"], p["sku"], p["days"], p["purchased_at"].astimezone(IST).date(),
                    t["n"], t["opens"], t["due"], t["status"], (t["failed"] or {}).get("note", ""), b.get("booking_id", ""), b.get("collection_date", ""),
                    b.get("slot", ""), t["resched"], t["late"], t["rcl_status"], t["report"] or "", (t["tap"] or {}).get("status", ""),
                    ASK.get(t["ask"], ""), t["confirmed"], t.get("sheet_st", ""), t.get("platform", ""), "yes" if t.get("other_platform") else "", t.get("done", "")]])
    return out.getvalue()


# ---------- Blood Test dashboard (the legacy sheet's dashboard, with our live tests folded in) ----------
BT_CSS = """
.btc{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:flex-end;margin:6px 0 14px}.btc .f{display:grid;gap:4px}.btc .f>span{font-size:12px;font-weight:600;color:var(--mute)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--r-sm);overflow:hidden;background:var(--card)}.seg button{background:transparent;color:var(--fg);border:0;border-radius:0;min-height:38px;font-weight:500}
.seg button[aria-pressed=true]{background:var(--pri);color:var(--on);font-weight:600}.quick{display:flex;flex-wrap:wrap;gap:6px}.quick button{min-height:32px;padding:4px 10px;font-size:13px;background:transparent;color:var(--fg);border-color:var(--line);font-weight:500}
.quick button[aria-pressed=true]{background:var(--pri);color:var(--on);border-color:var(--pri)}
.dd{position:relative}.dd>summary{list-style:none;cursor:pointer;border:1px solid var(--line);border-radius:var(--r-sm);padding:8px 10px;min-height:40px;background:var(--card);min-width:150px}
.dd>summary::-webkit-details-marker{display:none}.dd .pop{position:absolute;z-index:20;top:44px;left:0;background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:8px;
min-width:230px;max-height:320px;overflow:auto;box-shadow:0 8px 24px rgba(0,0,0,.15)}.dd .pop label{display:flex;gap:8px;align-items:center;font-weight:500;padding:3px 2px}.dd .pop input[type=checkbox]{min-height:0}
.cards{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin:0 0 12px}.cardb{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);
border-radius:var(--r);padding:14px;text-align:left;color:var(--fg);font-weight:400;display:flex;flex-direction:column;justify-content:flex-start;align-items:stretch;gap:2px;min-height:0;width:100%;box-shadow:var(--sh1)}.cardb:hover{filter:none;background:var(--muteb)}
.cardb .l{font-size:12px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.4px}.cardb .n{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums}
.cardb .s{font-size:12px;color:var(--mute)}.grpl{font-size:11px;font-weight:700;color:var(--mute);text-transform:uppercase;letter-spacing:.6px;margin:4px 0 8px}
.sub{border-left:3px solid var(--line);padding-left:12px;margin:0 0 12px 6px}.bars{display:grid;gap:6px}.bars .b{display:grid;grid-template-columns:150px 1fr 70px;gap:8px;align-items:center;font-size:13px}
.bars .t{height:12px;border-radius:6px;background:var(--muteb);overflow:hidden}.bars .t i{display:block;height:100%}.od{color:var(--bad);font-weight:600;font-size:12px}
dialog.modal{width:min(1200px,96vw);max-height:88vh;border:1px solid var(--line);border-radius:var(--r-lg);background:var(--card);color:var(--fg);padding:0}
dialog.modal::backdrop{background:rgba(0,0,0,.45)}dialog.modal .hd{display:flex;gap:10px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--card)}
dialog.modal .bd{padding:0 0 8px}.pg{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:10px}.pg button{min-height:32px;padding:4px 10px}
.c-done{border-left-color:#059669}.c-booked{border-left-color:#1d4ed8}.c-inprogress{border-left-color:#0ea5e9}.c-followup{border-left-color:#d97706}.c-noaction{border-left-color:#64748b}
.nw{white-space:nowrap}.c-blank{border-left-color:#f59e0b}.c-noteligible{border-left-color:#94a3b8}.c-notserviceable{border-left-color:#dc2626}.c-total{border-left-color:var(--pri)}
"""

BT_JS = r"""<script>
(function(){
const $=i=>document.getElementById(i), MN=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], PG=30;
const esc=v=>String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>{if(!s)return '—';const [y,m,d]=s.split('-');return (+d)+' '+MN[m-1]+' '+y.slice(2)};
const isoD=d=>d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
const GROUPS={done:['Done','Report shared / uploaded','#059669'],booked:['Blocked / Booked','Booked · Sample collected · Blocked','#1d4ed8'],
 inprogress:['In Progress','Spoken + test form shared · booking WhatsApp sent','#0ea5e9'],followup:['Follow Up','Will do in future · Follow up required · Need to reschedule · cancelled','#d97706'],
 noaction:['No Action Required','Denied by client','#64748b'],blank:['No Response','No status entered · not contacted yet','#f59e0b'],
 noteligible:['Not Eligible','Not eligible by rule · Program refund · Dropout','#94a3b8'],notserviceable:['Not Serviceable','Non-serviceable · International client','#dc2626']};
let D,R=[],TODAY,S={test:'bt1',basis:'due',quick:'this_month',from:'',to:'',plans:new Set(),coaches:new Set(),src:'',excl:false,q:'',page:1,pf:''},M={rows:[],type:'',page:1,title:''};
function load(){fetch('bt.json',{credentials:'same-origin'}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}).then(j=>{D=j;TODAY=j.meta.today;
 R=j.rows.map(a=>{const o={};j.cols.forEach((c,i)=>o[c]=a[i]);o.b1_el=!!o.b1_el;o.b2_el=!!o.b2_el;o.excluded=!!o.excluded;return o});
 readHash();build();setQuick(S.quick||'',true);}).catch(e=>{$('bt-main').innerHTML='<p class="banner bad">Couldn\'t load the data: '+esc(e.message)+'. Reload the page.</p>'})}
function readHash(){const h=new URLSearchParams(location.hash.slice(1));if(!h.toString())return;
 ['test','basis','quick','from','to','src','q','pf'].forEach(k=>{if(h.get(k)!=null)S[k]=h.get(k)});S.excl=h.get('excl')==='1';
 if(h.get('plans'))S.plans=new Set(h.get('plans').split('|'));if(h.get('coaches'))S.coaches=new Set(h.get('coaches').split('|'));}
function writeHash(){const h=new URLSearchParams();['test','basis','quick','src','q','pf'].forEach(k=>{if(S[k])h.set(k,S[k])});if(!S.quick){h.set('from',S.from);h.set('to',S.to)}
 if(S.excl)h.set('excl','1');if(S.plans.size)h.set('plans',[...S.plans].join('|'));if(S.coaches.size)h.set('coaches',[...S.coaches].join('|'));history.replaceState(null,'','#'+h)}
function range(key){const t=new Date(TODAY+'T00:00:00'),y=t.getFullYear(),m=t.getMonth(),add=n=>{const d=new Date(t);d.setDate(d.getDate()+n);return d};
 return {today:[t,t],next7:[t,add(6)],next30:[t,add(29)],this_month:[new Date(y,m,1),new Date(y,m+1,0)],last_month:[new Date(y,m-1,1),new Date(y,m,0)],
  last_3m:[new Date(y,m-2,1),new Date(y,m+1,0)],last_6m:[new Date(y,m-5,1),new Date(y,m+1,0)],all:[new Date(2020,0,1),new Date(y+2,11,31)]}[key]}
function setQuick(key,first){if(key){const r=range(key);if(r){S.from=isoD(r[0]);S.to=isoD(r[1])}}S.quick=key;$('from').value=S.from;$('to').value=S.to;$('day').value=S.from===S.to?S.from:'';
 document.querySelectorAll('#quick button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.k===key));S.page=1;render()}
function opts(id,vals,set,label){const box=$(id);box.querySelector('.pop .o').innerHTML=vals.map(v=>'<label><input type=checkbox value="'+esc(v)+'"'+(set.has(v)?' checked':'')+'>'+esc(v||'(blank)')+'</label>').join('');
 box.querySelector('summary').textContent=set.size?(set.size===1?[...set][0]||'(blank)':set.size+' '+label+' selected'):'All '+label;}
function build(){const plans=[...new Set(R.map(r=>r.plan))].sort(),coaches=[...new Set(R.map(r=>r.coach))].sort();
 opts('dplan',plans,S.plans,'plans');opts('dcoach',coaches,S.coaches,'coaches');
 ['test','basis'].forEach(k=>document.querySelectorAll('[data-'+k+']').forEach(b=>b.setAttribute('aria-pressed',b.dataset[k]===S[k])));
 $('src').value=S.src;$('pf').value=S.pf||'';$('excl').checked=S.excl;$('q').value=S.q;
 const m=D.meta;$('meta').textContent='Legacy sheet: '+m.legacy_rows+' rows'+(m.legacy_dups?' ('+m.legacy_dups+' exact duplicate rows left out)':'')+' · '+m.matched+' of our programs matched to sheet rows by order id · '+m.live+' programs only in our system · sheet copy from '+((m.imported['Blood Test']||['never'])[0])+' IST (synced hourly) \u00b7 done dates marked \u2248 are estimated: the sheet has no date for them';}
function wire(){document.querySelectorAll('[data-test]').forEach(b=>b.onclick=()=>{S.test=b.dataset.test;document.querySelectorAll('[data-test]').forEach(x=>x.setAttribute('aria-pressed',x===b));S.page=1;render()});
 document.querySelectorAll('[data-basis]').forEach(b=>b.onclick=()=>{S.basis=b.dataset.basis;document.querySelectorAll('[data-basis]').forEach(x=>x.setAttribute('aria-pressed',x===b));S.page=1;render()});
 document.querySelectorAll('#quick button').forEach(b=>b.onclick=()=>setQuick(b.dataset.k));
 $('from').onchange=$('to').onchange=()=>{S.from=$('from').value;S.to=$('to').value;if(S.from&&S.to&&S.from>S.to)[S.from,S.to]=[S.to,S.from];setQuick('')};
 $('day').onchange=()=>{if($('day').value){S.from=S.to=$('day').value;setQuick('')}};
 [['dplan','plans','plans'],['dcoach','coaches','coaches']].forEach(([id,k,l])=>{$(id).addEventListener('change',e=>{if(e.target.type!=='checkbox')return;e.target.checked?S[k].add(e.target.value):S[k].delete(e.target.value);
  $(id).querySelector('summary').textContent=S[k].size?(S[k].size===1?[...S[k]][0]||'(blank)':S[k].size+' '+l+' selected'):'All '+l;S.page=1;render()});
  $(id).querySelector('[data-all]').onclick=()=>{S[k].clear();$(id).querySelectorAll('input[type=checkbox]').forEach(c=>c.checked=false);$(id).querySelector('summary').textContent='All '+l;render()}});
 $('csearch').oninput=()=>{const v=$('csearch').value.toLowerCase();$('dcoach').querySelectorAll('.o label').forEach(l=>l.hidden=!l.textContent.toLowerCase().includes(v))};
 $('src').onchange=()=>{S.src=$('src').value;S.page=1;render()};$('pf').onchange=()=>{S.pf=$('pf').value;S.page=1;render()};$('excl').onchange=()=>{S.excl=$('excl').checked;S.page=1;render()};
 let tq;$('q').oninput=()=>{clearTimeout(tq);tq=setTimeout(()=>{S.q=$('q').value.trim().toLowerCase();S.page=1;render()},200)};
 $('export').onclick=exportView;$('mexport').onclick=()=>download(csv(M.rows,M.type),'SOVA_'+M.title.replace(/[^a-z0-9]+/gi,'_')+'_'+TODAY+'.csv');
 $('mclose').onclick=()=>$('dlg').close();document.addEventListener('click',e=>{document.querySelectorAll('details.dd[open]').forEach(d=>{if(!d.contains(e.target))d.open=false})});
 window.addEventListener('hashchange',()=>{});}
const inR=s=>!!s&&s>=S.from&&s<=S.to;
function base(r){if(!S.excl&&r.excluded)return false;if(S.src&&(S.src==='legacy'?r.src==='live':S.src==='live'?r.src==='legacy':false))return false;
 if(S.plans.size&&!S.plans.has(r.plan))return false;if(S.coaches.size&&!S.coaches.has(r.coach))return false;
 if(S.q&&![r.name,r.order,r.coach,r.plan,r.phone].some(v=>String(v||'').toLowerCase().includes(S.q)))return false;return true}
const OTHER=v=>!!v&&v!=='Redcliffe';
function pfOk(v){return !S.pf||(S.pf==='redcliffe'?v==='Redcliffe':S.pf==='other'?OTHER(v):S.pf==='own'?v==="Customer's own lab":S.pf==='none'?!v:v===S.pf)}
function testRows(n){const due='b'+n+'_due',done='b'+n+'_done';return R.filter(r=>base(r)&&pfOk(r['b'+n+'_pf'])&&inR(S.basis==='done'?r[done]:r[due]))}
function segment(rows,n){const sg={done:[],booked:[],inprogress:[],followup:[],noaction:[],notserviceable:[],noteligible:[],blank:[]};
 rows.forEach(r=>{const k=r['b'+n+'_k'],el=r['b'+n+'_el'];(!el&&k!=='notserviceable'&&k!=='done'?sg.noteligible:sg[k]).push(r)});return sg}
const pct=(v,t)=>t?Math.round(v*100/t)+'%':'0%';
function rangeLabel(){return (S.basis==='done'?'Done ':'Due ')+fmt(S.from)+' → '+fmt(S.to)+(S.plans.size?' · '+[...S.plans].join(', '):' · All plans')}
let BUCKET={};
function render(){if(!D)return;writeHash();BUCKET={};if(S.test==='both')return renderCombo();
 const n=S.test==='bt1'?1:2,all=testRows(n),sg=segment(all,n),T='bt'+n;
 const elig=[...sg.done,...sg.booked,...sg.inprogress,...sg.followup,...sg.noaction,...sg.blank],inel=[...sg.noteligible,...sg.notserviceable];
 const pend=elig.filter(r=>!['done','noaction'].includes(r['b'+n+'_k'])).sort((a,b)=>(a['b'+n+'_due']||'').localeCompare(b['b'+n+'_due']||''));
 const put=(k,rows)=>{BUCKET[k]=rows;return k};
 let h='<h2 class=m0>Blood Test '+n+'</h2><p class="m small">'+esc(rangeLabel())+' · '+(n===1?'BT1 due = program start date (sheet) / booking deadline (our system)':'BT2 due = 15 days before the program end (sheet and our system)')+'</p>';
 h+='<div class=cards>'+cardB(put('all',all),'c-total','Total cases',all.length,(S.basis==='done'?'Done in range':'Due in range'),'Includes not eligible & not serviceable')
  +cardB(put('elig',elig),'c-total','Eligible',elig.length,'Excl. not eligible & not serviceable','Sum of the breakdown below')
  +cardB(put('inel',inel),'c-noteligible','Not eligible / not serviceable',inel.length,'Not eligible: '+sg.noteligible.length+' · Not serviceable: '+sg.notserviceable.length,'')+'</div>';
 h+='<div class=sub><div class=grpl>Breakdown of eligible</div><div class=cards>'+['done','booked','inprogress','followup','noaction','blank'].map(k=>cardB(put(k,sg[k]),'c-'+k,GROUPS[k][0],sg[k].length,pct(sg[k].length,elig.length)+' of eligible',GROUPS[k][1])).join('')+'</div></div>';
 h+='<div class=sub><div class=grpl>Breakdown of not eligible / not serviceable</div><div class=cards>'+['noteligible','notserviceable'].map(k=>cardB(put(k,sg[k]),'c-'+k,GROUPS[k][0],sg[k].length,pct(sg[k].length,all.length)+' of total',GROUPS[k][1])).join('')+'</div></div>';
 h+='<section class="card" style="margin-bottom:12px"><h2>BT'+n+' status breakdown (eligible)</h2><div class=bars>'+['done','booked','inprogress','followup','noaction','blank'].map(k=>'<div class=b><span>'+GROUPS[k][0]+'</span><span class=t><i style="width:'+(elig.length?sg[k].length*100/elig.length:0)+'%;background:'+GROUPS[k][2]+'"></i></span><span class=m>'+sg[k].length+' · '+pct(sg[k].length,elig.length)+'</span></div>').join('')+'</div></section>';
 BUCKET.pend=pend;const pages=Math.max(1,Math.ceil(pend.length/PG));S.page=Math.min(S.page,pages);
 h+='<section class="card" style="margin-bottom:12px;padding:0"><div class=row style="padding:14px 16px"><h2 class=m0>BT'+n+' — Pending / not done <span style="color:var(--bad)">('+pend.length+')</span></h2><span class="m small" style="margin-left:auto">Oldest due first · '+pend.filter(r=>(r['b'+n+'_due']||'9')<TODAY).length+' overdue</span><button class=ghost data-modal="'+T+'|pend|Pending">Open all</button></div>'
  +table(pend.slice((S.page-1)*PG,S.page*PG),T)+pager(pages)+'</section>';
 h+=monthTable(n);
 $('bt-main').innerHTML=h;hookCards(T);}
function cardB(key,cls,label,n,sub,hint){const T=S.test==='both'?'btc':'bt'+(S.test==='bt1'?1:2);return '<button class="cardb '+cls+'" data-modal="'+T+'|'+key+'|'+esc(label)+'"><div class=l>'+esc(label)+'</div><div class=n>'+n+'</div><div class=s>'+esc(sub)+'</div>'+(hint?'<div class=s>'+esc(hint)+'</div>':'')+'</button>'}
function hookCards(){document.querySelectorAll('[data-modal]').forEach(b=>b.onclick=()=>{const [t,k,l]=b.dataset.modal.split('|');openModal(l,BUCKET[k]||[],t)});
 document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{S.page=+b.dataset.page;render();scrollTo({top:0,behavior:'smooth'})})}
function pager(pages){if(pages<=1)return '';let s='<div class=pg>';for(let i=1;i<=Math.min(pages,10);i++)s+='<button class="'+(i===S.page?'':'ghost')+'" data-page='+i+'>'+i+'</button>';
 return s+(pages>10?'<span class=m>… '+pages+' pages</span>':'')+'<span class="m small">Page '+S.page+' of '+pages+'</span></div>'}
function badge(k,st){const c={done:'ok',booked:'info',inprogress:'info',followup:'warn',noaction:'mute',notserviceable:'bad',noteligible:'mute',blank:'mute'}[k]||'mute';return '<span class="chip '+c+'">'+esc(st||'—')+'</span>'}
function who(r){const id=r.pid||r.lid;return '<b>'+(id?'<a href="p?id='+encodeURIComponent(id)+'">'+esc(r.name||'—')+'</a>':esc(r.name||'—'))+'</b><br><span class="m small">'+esc(r.phone)+'</span>'}
const SRC={legacy:'Sheet',live:'Live',both:'Sheet + live'};
function table(rows,type){if(!rows.length)return '<p class=m style="padding:0 16px 14px">No records in this range.</p>';
 if(type==='btc')return '<div class=wrap style="border:0;border-radius:0"><table><thead><tr><th>Client</th><th>Order</th><th>Plan</th><th>Duration</th><th>Coach</th><th>Prog status</th><th>BT1 status</th><th>BT2 status</th><th>Start</th><th>End (BT2 due)</th><th>Source</th></tr></thead><tbody>'+rows.map(r=>
  '<tr><td>'+who(r)+'</td><td>'+esc(r.order)+'</td><td>'+esc(r.plan)+'</td><td>'+r.dur+'d</td><td>'+esc(r.coach||'—')+'</td><td>'+esc(r.pstat||'—')+'</td><td>'+badge(r.b1_k,r.b1_st)+'<div class="m small">'+esc(r.b1_rm)+'</div></td><td>'+badge(r.b2_k,r.b2_st)+'<div class="m small">'+esc(r.b2_rm)+'</div></td><td class=nw>'+fmt(r.start)+'</td><td class=nw><b>'+fmt(r.b2_due)+'</b></td><td class=small>'+SRC[r.src]+'</td></tr>').join('')+'</tbody></table></div>';
 const n=type==='bt1'?1:2,b='b'+n+'_';
 return '<div class=wrap style="border:0;border-radius:0"><table><thead><tr><th>Client</th><th>Order</th><th>Plan</th>'+(n===1?'<th>Prev plan</th>':'')+'<th>Duration</th><th>Coach</th><th>Prog status</th><th>BT'+n+' due</th><th>Done on</th><th>Start</th><th>End</th><th>Lab / platform</th><th>Remark / follow-ups</th><th>BT'+n+' status</th><th>Source</th></tr></thead><tbody>'+rows.map(r=>{
  const od=r[b+'k']!=='done'&&r[b+'due']&&r[b+'due']<TODAY?Math.round((new Date(TODAY)-new Date(r[b+'due']))/864e5):0;
  return '<tr><td>'+who(r)+'</td><td>'+esc(r.order)+'</td><td>'+esc(r.plan)+'</td>'+(n===1?'<td class=small>'+esc(r.prev||'—')+'</td>':'')+'<td>'+r.dur+'d</td><td>'+esc(r.coach||'—')+'</td><td>'+esc(r.pstat||'—')+'</td><td class=nw><b>'+fmt(r[b+'due'])+'</b>'+(od?'<div class=od>'+od+'d overdue</div>':'')+'</td><td class=nw>'+(r[b+'dq']==='approx'?'<span title="No date in the sheet: the due date">\u2248 </span>':'')+fmt(r[b+'done'])+'</td><td class=nw>'+fmt(r.start)+'</td><td class=nw>'+fmt(r.end)+'</td><td class=small>'+(r[b+'pf']?esc(r[b+'pf'])+(OTHER(r[b+'pf'])&&r[b+'k']==='done'?' <span class="chip warn">Other platform</span>':''):'<span class=m>not recorded</span>')+'</td><td class=small style="max-width:220px">'+esc(r[b+'rm'])+(r[b+'fu']?'<div class=m>'+esc(r[b+'fu'])+'</div>':'')+'</td><td>'+badge(r[b+'k'],r[b+'st'])+'</td><td class=small>'+SRC[r.src]+(r.row?'<div class=m>row '+r.row+'</div>':'')+'</td></tr>'}).join('')+'</tbody></table></div>'}
function openModal(title,rows,type){M={rows,type,page:1,title};$('mtitle').textContent=title;$('msub').textContent=rangeLabel()+' — '+rows.length+' records';mrender();$('dlg').showModal()}
function mrender(){const pages=Math.max(1,Math.ceil(M.rows.length/100));$('mbody').innerHTML=table(M.rows.slice((M.page-1)*100,M.page*100),M.type)+(pages>1?'<div class=pg>'+Array.from({length:Math.min(pages,20)},(_,i)=>'<button class="'+(i+1===M.page?'':'ghost')+'" data-mp='+(i+1)+'>'+(i+1)+'</button>').join('')+'<span class="m small">100 per page</span></div>':'');
 $('mbody').querySelectorAll('[data-mp]').forEach(b=>b.onclick=()=>{M.page=+b.dataset.mp;mrender()})}
function cside(st,k,isBT2){const v=(st||'').trim().toLowerCase();if(k==='done')return 'done';if(['program refund','dropout','non-serviceable','international client'].includes(v)||k==='notserviceable')return 'out';
 if(isBT2&&v==='not eligible')return 'out';if(k==='noaction'||k==='noteligible')return 'noaction';return 'pending'}
function renderCombo(){const baseRows=R.filter(r=>base(r)&&(pfOk(r.b1_pf)||pfOk(r.b2_pf))&&r.b2_el&&(S.basis==='done'?(inR(r.b1_done)||inR(r.b2_done)):inR(r.b2_due)));
 const all=baseRows.filter(r=>cside(r.b1_st,r.b1_k,false)!=='out'&&cside(r.b2_st,r.b2_k,true)!=='out'),d1=r=>r.b1_k==='done',d2=r=>r.b2_k==='done';
 const both=all.filter(r=>d1(r)&&d2(r)),a=all.filter(r=>d1(r)&&!d2(r)),b=all.filter(r=>!d1(r)&&d2(r)),c=all.filter(r=>!d1(r)&&!d2(r));
 const need=all.filter(r=>cside(r.b1_st,r.b1_k,false)==='pending'||cside(r.b2_st,r.b2_k,true)==='pending').sort((x,y)=>(d1(x)+d2(x))-(d1(y)+d2(y)));
 const ns=new Set(need),na=all.filter(r=>!ns.has(r)&&!(d1(r)&&d2(r)));Object.assign(BUCKET,{all,both,a,b,c,need,na});
 let h='<h2 class=m0>Blood Test — BT1 vs BT2 tracker</h2><p class="m small">'+esc(rangeLabel())+' · programs ending in range, 90d+ only</p>';
 h+='<div class=cards>'+cardB('all','c-total','Total (90d+)',all.length,'Excl. '+(baseRows.length-all.length)+' not eligible','Program refund · Dropout · Not serviceable left out')
  +cardB('both','c-done','BT1 + BT2 done',both.length,pct(both.length,all.length),'')+cardB('a','c-followup','BT1 done · BT2 pending',a.length,pct(a.length,all.length),'')
  +cardB('b','c-booked','BT1 not done · BT2 done',b.length,pct(b.length,all.length),'')+cardB('c','c-notserviceable','Both pending',c.length,pct(c.length,all.length),'')+'</div>';
 h+='<div class=cards style="max-width:560px">'+cardB('need','c-notserviceable','Needs action',need.length,'At least one test still chaseable','')+cardB('na','c-noaction','No action required',na.length,'Denied by client, nothing left to chase','')+'</div>';
 const pages=Math.max(1,Math.ceil(need.length/PG));S.page=Math.min(S.page,pages);
 h+='<section class="card" style="padding:0"><div class=row style="padding:14px 16px"><h2 class=m0>Needs action <span style="color:var(--bad)">('+need.length+')</span></h2><span class="m small" style="margin-left:auto">Both-pending first</span></div>'+table(need.slice((S.page-1)*PG,S.page*PG),'btc')+pager(pages)+'</section>';
 $('bt-main').innerHTML=h;hookCards('btc')}
function monthTable(n){const k='b'+n+'_',by={};R.filter(r=>base(r)&&r[k+(S.basis==='done'?'done':'due')]&&inR(r[k+(S.basis==='done'?'done':'due')])).forEach(r=>{const m=r[k+(S.basis==='done'?'done':'due')].slice(0,7);(by[m]=by[m]||[]).push(r)});
 const ms=Object.keys(by).sort();if(ms.length<2)return '';
 return '<section class="card" style="padding:0"><div style="padding:14px 16px"><h2 class=m0>BT'+n+' by month ('+(S.basis==='done'?'done':'due')+' month)</h2></div><div class=wrap style="border:0;border-radius:0"><table><thead><tr><th>Month</th><th>Total</th><th>Eligible</th><th>Done</th><th>Booked</th><th>In progress</th><th>Follow up</th><th>No action</th><th>No response</th><th>Not eligible</th><th>Not serviceable</th><th>Done % of eligible</th></tr></thead><tbody>'
  +ms.map(m=>{const sg=segment(by[m],n),el=by[m].length-sg.noteligible.length-sg.notserviceable.length,[y,mo]=m.split('-');
  return '<tr><td>'+MN[mo-1]+' '+y+'</td><td>'+by[m].length+'</td><td>'+el+'</td>'+['done','booked','inprogress','followup','noaction','blank','noteligible','notserviceable'].map(x=>'<td>'+sg[x].length+'</td>').join('')+'<td><b>'+pct(sg.done.length,el)+'</b></td></tr>'}).join('')+'</tbody></table></div></section>'}
function csv(rows,type){const e=v=>{let s=v==null?'':String(v);if(/^[=+\-@\t\r]/.test(s))s="'"+s;return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s};
 let H,G;if(type==='btc'){H=['Name','Phone','Order ID','Plan','Duration','Coach','Program Status','BT1 Status','BT1 Remark','BT2 Status','BT2 Remark','Start Date','End Date','Source'];
  G=r=>[r.name,r.phone,r.order,r.plan,r.dur,r.coach,r.pstat,r.b1_st,r.b1_rm,r.b2_st,r.b2_rm,r.start,r.end,SRC[r.src]]}
 else{const n=type==='bt1'?1:2,b='b'+n+'_';H=['Name','Phone','Order ID','Plan'].concat(n===1?['Prev Plan']:[]).concat(['Duration','Coach','Program Status','BT'+n+' Due','BT'+n+' Done On','Done Date Source','Start Date','End Date','BT'+n+' Lab / Platform','BT'+n+' Other Platform','BT'+n+' Remark','BT'+n+' Follow-ups','BT'+n+' Status','Source','Sheet Row']);
  G=r=>[r.name,r.phone,r.order,r.plan].concat(n===1?[r.prev]:[]).concat([r.dur,r.coach,r.pstat,r[b+'due'],r[b+'done'],r[b+'dq'],r.start,r.end,r[b+'pf'],OTHER(r[b+'pf'])&&r[b+'k']==='done'?'yes':'',r[b+'rm'],r[b+'fu'],r[b+'st'],SRC[r.src],r.row||''])}
 return [H.join(',')].concat(rows.map(r=>G(r).map(e).join(','))).join('\n')}
function download(text,name){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿'+text],{type:'text/csv;charset=utf-8'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000)}
function exportView(){if(S.test==='both')return download(csv(BUCKET.all||[],'btc'),'SOVA_BT_Combined_'+S.from+'_'+S.to+'.csv');const n=S.test==='bt1'?1:2;download(csv(BUCKET.all||[],'bt'+n),'SOVA_BT'+n+'_'+S.basis+'_'+S.from+'_'+S.to+'.csv')}
wire();load();
})();
</script>"""


def bt_page(who, mode, hold=""):
    quick = "".join("<button type=button data-k=%s aria-pressed=false>%s</button>" % (k, l) for k, l in (
        ("today", "Today"), ("next7", "Next 7 days"), ("next30", "Next 30 days"), ("this_month", "This month"), ("last_month", "Last month"),
        ("last_3m", "Last 3 months"), ("last_6m", "Last 6 months"), ("all", "All")))
    dd = lambda i, l, extra="": ("<details class=dd id=%s><summary>All %s</summary><div class=pop>%s<div class=row style='justify-content:space-between'><b class=small>Select %s</b>"
                                 "<button type=button class=ghost data-all style='min-height:28px;padding:2px 8px'>All</button></div><div class=o></div></div></details>" % (i, l, extra, l))
    body = ("<div class=row style='margin-bottom:4px'><h1 class=m0>Blood Test dashboard</h1><button id=export class=ghost style='margin-left:auto'>&#8595; Export CSV (this view)</button>"
            "<a class='btn ghost' href=legacy>Legacy sheet data</a><a class='btn ghost' href=legacy-issues>Sheet data issues</a></div><p class='m small' id=meta>Loading...</p>"
            "<div class=btc><div class=f><span>Test</span><div class=seg role=group aria-label=Test><button type=button data-test=bt1>BT1</button><button type=button data-test=bt2>BT2</button>"
            "<button type=button data-test=both>BT1 vs BT2</button></div></div>"
            "<div class=f><span>Date is the</span><div class=seg role=group aria-label='Date basis'><button type=button data-basis=due>Due date</button><button type=button data-basis=done>Done date</button></div></div>"
            "<div class=f><label for=from><span class='small m'>From</span></label><input type=date id=from></div><div class=f><label for=to><span class='small m'>To</span></label><input type=date id=to></div>"
            "<div class=f><label for=day><span class='small m'>Specific day</span></label><input type=date id=day></div>"
            "<div class=f style='flex-basis:100%%'><span>Quick range</span><div class=quick id=quick>%s</div></div>"
            "<div class=f><span>Plan</span>%s</div><div class=f><span>Coach</span>%s</div>"
            "<div class=f><label for=src><span class='small m'>Source</span></label><select id=src><option value=''>Sheet + live</option><option value=legacy>Legacy sheet</option>"
            "<option value=live>Live (our system)</option></select></div>"
            "<div class=f><label for=q><span class='small m'>Search</span></label><input id=q type=search placeholder='Name, order, coach, phone'></div>"
            "<div class=f><label for=pf><span class='small m'>Lab / platform</span></label><select id=pf><option value=''>All</option>"
            "<option value=redcliffe>Redcliffe (ours)</option><option value=other>Other platform (any)</option><option value=Healthians>Healthians</option>"
            "<option value=Metropolis>Metropolis</option><option value=Thyrocare>Thyrocare</option><option value=own>Customer's own lab</option>"
            "<option value=none>Not recorded</option></select></div>"
            "<label class=radio style='align-self:center'><input type=checkbox id=excl><span>Include programs not started / dropped out / refunded / on break</span></label></div>"
            "<div id=bt-main><p class=m>Loading blood tests...</p></div>"
            "<dialog class=modal id=dlg aria-labelledby=mtitle><div class=hd><div><h2 class=m0 id=mtitle></h2><div class='m small' id=msub></div></div>"
            "<button id=mexport class=ghost style='margin-left:auto'>&#8595; Export CSV</button><button id=mclose class=ghost aria-label=Close>&#10005;</button></div><div class=bd id=mbody></div></dialog>"
            % (quick, dd("dplan", "plans"), dd("dcoach", "coaches", "<input id=csearch type=search placeholder='Search coach' style='width:100%;min-height:32px;margin-bottom:6px'>")))
    return frame(who, mode, "<style>%s</style>%s%s" % (BT_CSS, body, BT_JS), "", hold)


def legacy_page(sheet, table, sheets, meta, qq, who, mode, hold=""):
    """A kept tab of the legacy sheet, as it was copied (search + CSV)."""
    seen = {m["sheet"]: m for m in meta}
    tabs = "".join("<a href='legacy?sheet=%s'%s>%s%s</a>" % (urllib.parse.quote(s), " aria-current=page" if s == sheet else "", e(s),
                                                              " (%d)" % seen[s]["n"] if s in seen else " (not copied)") for s in sheets)
    head, rows = (table[0], table[1:]) if table else ([], [])
    ql = (qq or "").strip().lower()
    hit = [(i, r) for i, r in enumerate(rows, 2) if not ql or ql in " ".join(r).lower()]
    m = seen.get(sheet)
    body = ("<div class=row><h1 class=m0>Legacy sheet: %s</h1><a class='btn ghost' style='margin-left:auto' href='legacy.csv?sheet=%s'>&#8595; CSV</a><a class='btn ghost' href=bt>Blood Test dashboard</a></div>"
            "<p class='m small'>Copied from the Google Sheet <b>Program Clean</b> into BigQuery (<code>sova.bt_legacy</code>)%s. Replace a tab from Settings &rarr; Legacy Google Sheet.</p>"
            "<nav class=filters aria-label=Tabs>%s</nav><form class=row method=get action=legacy><input type=hidden name=sheet value='%s'><input name=q type=search value='%s' placeholder='Search this tab' aria-label=Search>"
            "<button class=ghost>Search</button><span class='m small'>%d of %d rows%s</span></form>"
            "<div class=wrap style='margin-top:10px;max-height:70vh'><table><thead><tr><th>#</th>%s</tr></thead><tbody>%s</tbody></table></div>"
            % (e(sheet), urllib.parse.quote(sheet), (" on %s by %s" % (e(str(m["at"])[:16]), e(m["by_user"]))) if m else " (not copied yet)", tabs, e(sheet), e(qq),
               len(hit), len(rows), " (first 1000 shown)" if len(hit) > 1000 else "",
               "".join("<th>%s</th>" % e(h) for h in head),
               "".join("<tr><td class=m>%d</td>%s</tr>" % (i, "".join("<td>%s</td>" % e(c) for c in r)) for i, r in hit[:1000])
               or "<tr><td colspan=99 class=m>No rows.</td></tr>"))
    return frame(who, mode, body, "", hold)


def legacy_section(meta, sync=None):
    """Settings: the legacy Google Sheet tabs kept in BigQuery, the hourly sync, and a CSV upload to replace a tab by hand."""
    import legacy_bt as LB
    seen = {m["sheet"]: m for m in meta or ()}
    li = "".join("<li><span><a href='legacy?sheet=%s'>%s</a></span><span class='m small'>%s</span></li>" % (
        urllib.parse.quote(s), e(s), ("%d rows · copied %s by %s" % (seen[s]["n"], e(str(seen[s]["at"])[:16]), e(seen[s]["by_user"]))) if s in seen else "not copied yet")
        for s in LB.SHEETS)
    if sync:
        bad = {k: v for k, v in (sync.get("detail") or {}).items() if str(v).startswith("failed")}
        st = "<p class='banner %s'>Last sync %s IST: %s</p>" % ("bad" if bad else "okb", e(str(sync["at"].astimezone(IST) if hasattr(sync["at"], "astimezone") else sync["at"])[:16]),
                                                             e("; ".join("%s %s" % kv for kv in (bad or sync.get("detail") or {}).items())))
    else: st = "<p class='banner warn'>The hourly sync hasn't run yet.</p>"
    return ("<section class='card stack' id=legacy><h2>Legacy Google Sheet (Program Clean)</h2><p class='small m'>Every hour (at :17) the sheet's tabs below are "
            "copied into BigQuery (<code>sova.bt_legacy</code>); a tab that hasn't changed is skipped. The sheet is shared read-only with this service's account. "
            "The <a href=bt>Blood Test dashboard</a> reads <b>Blood Test</b>; <b>Program-Main</b> fills in customer details (program, onboarding, consultation) on their profiles. Rows the team should fix in the sheet: <a href=legacy-issues>sheet data issues</a>.</p>%s<ul class=list>%s</ul>"
            "<form method=post action=legacy class=row data-once><input type=hidden name=a value=sync><button data-busy='Syncing...'>Sync now</button></form>"
            "<details><summary class='btn ghost'>Replace a tab by hand (CSV)</summary><form method=post action=legacy enctype=multipart/form-data class=stack data-once>"
            "<div class=row><label for=lg_sheet>Tab</label><select id=lg_sheet name=sheet>%s</select>"
            "<input type=file name=file accept='.csv,text/csv' required aria-label='CSV file'><button data-busy='Uploading...'>Upload CSV</button></div></form></details></section>"
            % (st, li, "".join("<option>%s</option>" % e(s) for s in LB.SHEETS)))


def legacy_issues(issues, kind, who, mode, hold=""):
    """Rows of the legacy sheet the team should fix (the panel cleans them on read; the sheet itself is never changed here)."""
    kinds = {}
    for i in issues: kinds[i["issue"]] = kinds.get(i["issue"], 0) + 1
    rows = [i for i in issues if not kind or i["issue"] == kind]
    tabs = "<a href=legacy-issues%s>All (%d)</a>" % ("" if kind else " aria-current=page", len(issues)) + "".join(
        "<a href='legacy-issues?issue=%s'%s>%s (%d)</a>" % (urllib.parse.quote(k), " aria-current=page" if k == kind else "", e(k), n) for k, n in sorted(kinds.items()))
    body = ("<div class=row><h1 class=m0>Legacy sheet: data issues</h1><a class='btn ghost' style='margin-left:auto' href=legacy-issues.csv>&#8595; CSV</a>"
            "<a class='btn ghost' href=bt>Blood Test dashboard</a></div><p class='m small'>From the <b>Blood Test</b> tab. The dashboard already handles these "
            "(duplicates left out, statuses and labs cleaned, missing done dates estimated from the due date and marked &asymp;); fixing them in the sheet makes "
            "the numbers exact. Row numbers are the sheet's.</p><nav class=filters aria-label='Issue type'>%s</nav>"
            "<div class=wrap><table><thead><tr><th>Row</th><th>Order</th><th>Name</th><th>Issue</th><th>What the cell says</th></tr></thead><tbody>%s</tbody></table></div>"
            % (tabs, "".join("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (i["row"], e(i["order"]), e(i["name"]), e(i["issue"]), e(i["detail"]))
                             for i in rows[:3000]) or "<tr><td colspan=5 class=m>No issues.</td></tr>"))
    return frame(who, mode, body, "", hold)


def sheet_history(rows, title):
    """The legacy sheet's rows for a person: per program, Test 1 and Test 2 with status, lab / platform, done date, remark and follow-ups."""
    out = []
    for r in sorted(rows, key=lambda r: r["start"] or r["paid"] or ""):
        tests = "".join(
            "<tr><td>Test %d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=small>%s</td></tr>" % (
                n, e(r["b%d_st" % n] or "-"), e(day(r["b%d_due" % n]) or "-"),
                (day(r["b%d_done" % n]) + (" <span class='m small'>(estimated)</span>" if r["b%d_dq" % n] == "approx" else "")) if r["b%d_done" % n] else "-",
                pf_chip(r["b%d_pf" % n], r["b%d_k" % n] == "done"), e(" | ".join(x for x in (r["b%d_rm" % n], r["b%d_fu" % n]) if x)) or "-")
            for n in (1, 2))
        out.append("<div class=stack><div class='row small'><b>%s</b><span class=m>%s · %s days · %s · coach %s · %s to %s · sheet row %d</span></div>"
                   "<div class=wrap><table><thead><tr><th>Test</th><th>Status</th><th>Due</th><th>Done</th><th>Lab / platform</th><th>Remark / follow-ups</th></tr></thead>"
                   "<tbody>%s</tbody></table></div></div>" % (e(r["order"] or "No order id"), e(r["plan"] or "-"), r["dur"], e(r["pstat"] or "-"), e(r["coach"] or "-"),
                                                               e(day(r["start"]) or "-"), e(day(r["end"]) or "-"), r["row"], tests))
    return "<section class='card stack'><h2>%s</h2>%s</section>" % (e(title), "".join(out))


def legacy_profile(me, hist, ops, here, who, mode, hold=""):
    """A person who is only in the legacy sheet: their details and every program and blood test the sheet has for them (read-only)."""
    import legacy_bt as LB
    pm = ops.get(LB.order_key(me["order"])) or {}
    ph = re.sub(r"\D", "", me["phone"] or "")
    links = "".join("<li><a href='p?id=%s'>%s</a> <span class='m small'>%s · %s</span></li>" % (urllib.parse.quote(x["program_id"]), e(x["order_name"]), e(x.get("product") or x["sku"]),
                                                                                         x["purchased_at"].astimezone(IST).strftime("%d %b %Y")) for x in here)
    info = ("<section class=card><h2>%s <span class='chip mute'>Legacy (sheet)</span></h2><dl><dt>Phone</dt><dd>%s</dd><dt>Alternate</dt><dd>%s</dd><dt>Email</dt><dd>%s</dd>"
            "<dt>Order</dt><dd>%s</dd><dt>Program</dt><dd>%s · %s days · %s</dd><dt>Paid</dt><dd>%s</dd><dt>Dates</dt><dd>%s to %s · %s</dd><dt>Coach</dt><dd>%s</dd>"
            "%s</dl><p class='small m'>From the legacy Google Sheet (Program Clean), synced every hour. Read-only here: this person gets no WhatsApps from this desk "
            "unless they buy a program again.</p></section>"
            % (e(me["name"] or "Customer"), ("<a href='tel:+%s'>%s</a>" % (e(ph), e(me["phone"]))) if ph else "<span class='chip bad'>No number</span>", e(me["alt"] or "-"),
               e(me["email"] or "-"), e(me["order"] or "-"), e(me["item"] or "-"), me["dur"], e(me["plan"] or "-"), e(day(me["paid"]) or "-"),
               e(day(me["start"]) or "-"), e(day(me["end"]) or "-"), e(me["pstat"] or "-"), e(me["coach"] or "-"),
               ("<dt>Dr. consults</dt><dd>DC1 %s %s · DC2 %s %s</dd>" % (e(pm.get("dc1_status") or "-"), e(pm.get("dc1_date") or ""), e(pm.get("dc2_status") or "-"),
                                                                      e(pm.get("dc2_date") or ""))) if pm else ""))
    body = ("<p class=small><a href='./?s=sheet'>&larr; Legacy (sheet)</a></p>%s%s%s"
            % (info, sheet_history(hist, "Blood tests in the sheet (%d program%s)" % (len(hist), "" if len(hist) == 1 else "s")),
               "<section class='card stack'><h2>Programs on this desk (same phone)</h2><ul class=list>%s</ul></section>" % links if links else ""))
    return frame(who, mode, body, "", hold)


def send_page(manual, key, tpl, vals, buttons, prog, n, a, msg, ok, recent, who, mode, hold=""):
    """Send any of our WhatsApp templates to a number now: pick the template, optionally a customer whose details (and links) to use,
    check or edit the values, see the message, send. Every send is logged with Briza's answer."""
    opts = "".join("<option value=%s%s>%s</option>" % (k, " selected" if k == key else "", e(TEMPLATE_TEXT[k][0])) for k, _, _ in manual)
    label, cat, text = TEMPLATE_TEXT.get(key, ("", "", ""))
    preview = e(text)
    for k, v in vals.items(): preview = preview.replace("{{%s}}" % k, "<mark data-v=%s>%s</mark>" % (k, e(v)))
    preview = re.sub(r"\{\{(\w+)\}\}", lambda m: "<mark>%s</mark>" % e(vals.get(m.group(1), "{{%s}}" % m.group(1))), preview).replace("\n", "<br>")
    who_ = ("<span class='chip ok'>Using %s's program</span> <a href='p?id=%s'>%s</a> · Test %d · the buttons open their own %s page"
            % (e(prog.get("customer_name") or "the customer"), urllib.parse.quote(prog["program_id"]), e(prog.get("order_name") or prog["program_id"]), n,
               "report" if key == "report_template" else "booking")) if prog else (
            "<span class='chip warn'>Sample values</span> <span class='m small'>No customer picked: the values are examples%s.</span>"
            % (" and the button opens a 'not found' page" if buttons else ""))
    if a.get("ref") and not prog: who_ = "<span class='chip bad'>No program found for %s</span> " % e(a.get("ref")) + who_
    hidden = "".join("<input type=hidden name=%s value='%s'>" % (k, e(a.get(k, ""))) for k in ("ref", "name"))
    fields = "".join("<div class=f><label for=v_%s>{{%s}}</label><input id=v_%s name=v_%s value='%s' data-k=%s></div>" % (k, k, k, k, e(v), k) for k, v in vals.items())
    def jd_(r):
        d = r.get("detail")
        try: return d if isinstance(d, dict) else json.loads(d or "{}")
        except ValueError: return {}
    def row(r):
        d = jd_(r)
        when = r["at"].astimezone(IST).strftime("%d %b %H:%M") if hasattr(r["at"], "astimezone") else str(r["at"])[:16]
        cust = ("<a href='p?id=%s'>%s</a>" % (urllib.parse.quote(r["program_id"]), e(r["program_id"]))) if r.get("program_id") not in (None, "", "_manual") else "sample"
        return ("<tr><td class=nw>%s</td><td>%s</td><td>%s</td><td><span class='chip %s'>%s</span></td><td>%s</td><td class=small>%s</td><td class=small>%s</td>"
                "<td class=small>%s</td></tr>" % (e(when), e(d.get("phone") or ""), e(d.get("template") or r.get("note") or ""), "ok" if r["status"] == "sent" else "bad",
                                                  e(r["status"]), cust, e(", ".join("%s=%s" % kv for kv in (d.get("params") or {}).items())[:160]),
                                                  e(str(d.get("result"))[:160]), e(r.get("by_user") or "")))
    rows = "".join(row(r) for r in recent)
    body = ("<h1>Send a WhatsApp</h1><p class='m small'>Sends one of our approved Briza templates to any number, right now (whatever the send mode). "
            "Pick a customer to use their name, dates and their own booking / report link; leave it empty to send example values. Admins only.</p>%s"
            "<section class='card stack'><h2>1. Number and template</h2><form method=get action=send class=stack>"
            "<div class=btc><div class=f><label for=phone>Mobile number</label><input id=phone name=phone inputmode=tel required value='%s' placeholder='98765 43210'></div>"
            "<div class=f><label for=template>Template</label><select id=template name=template onchange='this.form.submit()'>%s</select></div>"
            "<div class=f><label for=ref>Use a customer's details (optional)</label><input id=ref name=ref value='%s' placeholder='Order #RE123, phone or program id'></div>"
            "<div class=f><label for=name>Name to use (optional)</label><input id=name name=name value='%s' placeholder='First name in the message'></div>"
            "<div class=f><span>&nbsp;</span><button class=ghost>Fill in the values</button></div></div></form></section>"
            "<section class='card stack'><h2>2. Check and send <span class='m small'>%s · %s</span></h2><p class=small>%s</p>"
            "<form method=post action=send class=stack data-once><input type=hidden name=phone value='%s'><input type=hidden name=template value='%s'>%s"
            "<div class=btc>%s</div><div class='card' style='background:var(--muteb)'><p class='small m0'><b>Message preview</b></p><p id=pv>%s</p><p class='small m'>%s</p></div>"
            "<div class=row><button data-busy='Sending...' %s>Send to %s</button></div></form></section>"
            "<section class='card stack' style='padding:0'><h2 style='padding:14px 16px 0'>Recent sends from this page</h2><div class=wrap style='border:0'><table>"
            "<thead><tr><th>When</th><th>Number</th><th>Template</th><th>Result</th><th>Customer</th><th>Values</th><th>Briza's answer</th><th>By</th></tr></thead>"
            "<tbody>%s</tbody></table></div></section>"
            "<script>document.querySelectorAll('[data-k]').forEach(i=>i.addEventListener('input',()=>{document.querySelectorAll('mark[data-v='+i.dataset.k+']')"
            ".forEach(m=>m.textContent=i.value)}))</script>"
            % (("<p class='banner %s'>%s</p>" % ("okb" if ok else "bad", e(msg))) if msg else "", e(a.get("phone", "")), opts, e(a.get("ref", "")), e(a.get("name", "")),
               e(tpl), e(cat), who_, e(a.get("phone", "")), e(key), hidden, fields or "<p class=m>This template has no values.</p>", preview,
               "Buttons: " + e(cat.split(";", 1)[1].strip()) if ";" in cat else "No buttons.", "" if a.get("phone") else "disabled",
               e(a.get("phone") or "the number above"), rows or "<tr><td colspan=8 class=m>Nothing sent from here yet.</td></tr>"))
    return frame(who, mode, body, "", hold)
