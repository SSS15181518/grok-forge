"""Download the official grok patterns (ECS v1 set, as used by Logstash 8/9 by default)
from logstash-patterns-core into data/patterns. Re-run to update."""
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/logstash-plugins/logstash-patterns-core/main/patterns/ecs-v1/"
FILES = ["grok-patterns", "httpd", "java", "linux-syslog", "firewalls", "haproxy", "aws", "bacula",
         "bind", "bro", "exim", "junos", "maven", "mcollective", "mongodb", "nagios", "postgresql",
         "rails", "redis", "ruby", "squid", "zeek"]
out = Path(__file__).resolve().parent.parent / "data" / "patterns"
out.mkdir(parents=True, exist_ok=True)
for f in FILES:
    try:
        (out / f).write_bytes(urllib.request.urlopen(BASE + f, timeout=30).read())
        print("ok  ", f)
    except Exception as e:  # noqa: BLE001
        print("skip", f, e)
