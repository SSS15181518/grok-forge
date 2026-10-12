"""Writes data/examples.jsonl with a starter set of verified examples.
Every example is matched against its own log lines; broken ones abort the script.
Add your own examples to data/examples.jsonl (one JSON object per line) or with
the "Save as example" button in the web app.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from grok_engine import PatternLibrary, evaluate  # noqa: E402

EX = [
 {"logs": ['2026-10-08 14:32:01,123 ERROR [http-nio-8080-exec-7] com.acme.OrderService - Order ORD-004521 failed: timeout after 30s',
           '2026-10-08 14:32:02,004 INFO  [main] com.acme.Bootstrap - Started in 4.2s'],
  "pattern": r'^%{TIMESTAMP_ISO8601:timestamp} %{LOGLEVEL:[log][level]}\s+\[%{DATA:[process][thread][name]}\] %{JAVACLASS:[log][logger]} - %{GREEDYDATA:message}$',
  "notes": "Log4j/Logback default layout; \\s+ handles padded level"},
 {"logs": ['10.1.2.3 - alice [08/Oct/2026:09:12:44 +0000] "GET /cart?id=9 HTTP/1.1" 200 512 "https://shop.acme.com/" "Mozilla/5.0 (X11; Linux x86_64)"'],
  "pattern": "%{COMBINEDAPACHELOG}", "notes": "Apache/Nginx combined format: use the built-in"},
 {"logs": ['192.168.0.9 - - [08/Oct/2026:09:12:44 +0530] "POST /api/login HTTP/1.1" 401 87 "-" "curl/8.5.0" 0.004'],
  "pattern": r'^%{IPORHOST:[source][address]} - (?:-|%{USER:[user][name]}) \[%{HTTPDATE:timestamp}\] "%{WORD:[http][request][method]} %{NOTSPACE:[url][original]} HTTP/%{NUMBER:[http][version]}" %{INT:[http][response][status_code]:int} (?:-|%{INT:[http][response][body][bytes]:int}) "(?:-|%{DATA:[http][request][referrer]})" "(?:-|%{DATA:[user_agent][original]})" %{NUMBER:[nginx][request_time]:float}$',
  "notes": "Nginx combined plus request_time"},
 {"logs": ['Oct  8 09:12:44 fw01 sshd[4242]: Failed password for root from 203.0.113.9 port 51515 ssh2'],
  "pattern": r'^%{SYSLOGTIMESTAMP:timestamp} %{SYSLOGHOST:[host][hostname]} %{DATA:[process][name]}(?:\[%{POSINT:[process][pid]:int}\])?: Failed password for (?:invalid user )?%{USERNAME:[user][name]} from %{IP:[source][ip]} port %{INT:[source][port]:int} %{WORD:[network][protocol]}$',
  "notes": "SSH failed login"},
 {"logs": ['Oct  8 09:12:44 fw01 kernel: [UFW BLOCK] IN=eth0 OUT= SRC=203.0.113.9 DST=10.0.0.5 PROTO=TCP SPT=51515 DPT=22'],
  "pattern": r'^%{SYSLOGTIMESTAMP:timestamp} %{SYSLOGHOST:[host][hostname]} %{DATA:[process][name]}: \[%{DATA:[rule][name]}\] %{GREEDYDATA:[fw][kv]}$',
  "notes": "Envelope only; parse the key=value tail with the kv filter"},
 {"logs": ['[2026-10-08T09:12:44.512Z] [WARN] user-service: cache miss key=user:42 latency=17ms'],
  "pattern": r'^\[%{TIMESTAMP_ISO8601:timestamp}\] \[%{LOGLEVEL:[log][level]}\] %{NOTSPACE:[service][name]}: %{GREEDYDATA:message}$',
  "notes": "Brackets must be escaped"},
 {"logs": ['2026-10-08T09:12:44Z level=error msg="db timeout" duration=1.52s request_id=7f3a9c'],
  "pattern": r'^%{TIMESTAMP_ISO8601:timestamp} level=%{LOGLEVEL:[log][level]} msg="%{DATA:message}" duration=%{NUMBER:[event][duration_s]:float}s request_id=%{NOTSPACE:[http][request][id]}$',
  "notes": "logfmt-like; quoted msg captured without quotes"},
 {"logs": ['550e8400-e29b-41d4-a716-446655440000 2026-10-08 09:12:44 PAYMENT_OK amount=129.90 currency=INR card=****4444'],
  "pattern": r'^%{UUID:[transaction][id]} %{TIMESTAMP_ISO8601:timestamp} %{WORD:[event][action]} amount=%{NUMBER:[payment][amount]:float} currency=%{WORD:[payment][currency]} card=%{NOTSPACE:[payment][card_masked]}$',
  "notes": "UUID first token"},
 {"logs": ['ERROR 2026-10-08 09:12:44 [worker-3] Job 8812 failed after 3 retries'],
  "pattern": r'^%{LOGLEVEL:[log][level]} %{TIMESTAMP_ISO8601:timestamp} \[%{DATA:[process][thread][name]}\] Job %{INT:[job][id]:int} failed after %{INT:[job][retries]:int} retries$',
  "notes": "Fixed message template with numbers"},
 {"logs": ['127.0.0.1:6379 08 Oct 2026 09:12:44.123 * Background saving started by pid 4242'],
  "pattern": r'^%{HOSTPORT:[redis][instance]} %{MONTHDAY} %{MONTH} %{YEAR} %{TIME} %{DATA:[redis][role]} %{GREEDYDATA:message}$',
  "notes": "Custom date pieces when no single date pattern fits"},
 {"logs": ['2026/10/08 09:12:44 [error] 1234#0: *5 open() "/var/www/favicon.ico" failed (2: No such file or directory), client: 10.0.0.7, server: acme.com'],
  "pattern": r'^(?<timestamp>%{YEAR}/%{MONTHNUM}/%{MONTHDAY} %{TIME}) \[%{LOGLEVEL:[log][level]}\] %{POSINT:[process][pid]:int}#%{INT:[process][thread][id]:int}: (?:\*%{INT:[nginx][connection_id]:int} )?%{DATA:[error][message]}(?:, client: %{IP:[source][ip]})?(?:, server: %{NOTSPACE:[url][domain]})?$',
  "notes": "Nginx error log with optional trailing parts"},
 {"logs": ['TX9981|2026-10-08 09:12:44|SUCCESS|842|acme-pay'],
  "pattern": r'^%{NOTSPACE:[transaction][id]}\|%{TIMESTAMP_ISO8601:timestamp}\|%{WORD:[event][outcome]}\|%{INT:[event][duration_ms]:int}\|%{NOTSPACE:[service][name]}$',
  "notes": "Pipe-delimited; dissect would also work"},
 {"logs": ['Thu Oct 08 09:12:44.123456 2026 [proxy:error] [pid 4242:tid 140] (111)Connection refused: AH00957: backend failed'],
  "pattern": r'^\[?%{DAY} %{MONTH} %{MONTHDAY} %{TIME} %{YEAR}\]? \[%{WORD:[apache][error][module]}:%{LOGLEVEL:[log][level]}\] \[pid %{POSINT:[process][pid]:int}(?::tid %{INT:[process][thread][id]:int})?\] %{GREEDYDATA:message}$',
  "notes": "Apache 2.4 error log variant"},
 {"logs": ['2026-10-08 09:12:44.123 UTC [4242] postgres@shop ERROR:  duplicate key value violates unique constraint "orders_pkey"'],
  "pattern": r'^%{TIMESTAMP_ISO8601:timestamp} %{WORD:[event][timezone]} \[%{POSINT:[process][pid]:int}\] %{USERNAME:[user][name]}@%{NOTSPACE:[postgresql][database]} %{LOGLEVEL:[log][level]}:\s+%{GREEDYDATA:message}$',
  "notes": "PostgreSQL log_line_prefix '%m [%p] %u@%d '"},
]


def main():
    lib = PatternLibrary(ROOT / "data" / "patterns")
    out = ROOT / "data" / "examples.jsonl"
    ok = True
    with out.open("w", encoding="utf-8") as f:
        for ex in EX:
            ev = evaluate(ex["pattern"], ex["logs"], lib, ex.get("pattern_definitions"))
            if ev["metrics"]["match_rate"] != 1.0:
                ok = False
                print("FAILS:", ex["pattern"], ev.get("error"), file=sys.stderr)
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    print(f"wrote {len(EX)} examples to {out}" + ("" if ok else " (with failures!)"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
