"""Generates the 55-payload benchmark corpus into benchmarks/corpus/.

Each payload is a JSON file: {id, category, text, facts}. `facts` records
the numbers, dates, and negation phrases actually present in `text`, so
`runner.py` can build a faithfulness matrix by checking which of those
facts survive each processing pass -- it never has to guess what counts
as a "fact", it checks against this ground truth extracted at authoring
time.

Categories (spec Sec 4): prose, code, html, tables, agent transcripts,
adversarial negations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CORPUS_DIR = Path(__file__).parent / "corpus"

_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2},? \d{4}\b")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
_NEGATION_RE = re.compile(r"\b(not|never|no longer|cannot|can't|didn't|doesn't|isn't|won't|without|neither|nor|none)\b", re.IGNORECASE)


def extract_facts(text: str) -> dict:
    return {
        "numbers": sorted(set(_NUMBER_RE.findall(text))),
        "dates": sorted(set(_DATE_RE.findall(text))),
        "negations": sorted(set(m.lower() for m in _NEGATION_RE.findall(text))),
    }


def payload(pid: str, category: str, text: str) -> dict:
    return {"id": pid, "category": category, "text": text, "facts": extract_facts(text)}


PROSE = [
    "The Wright brothers achieved the first sustained, controlled, powered flight on December 17, 1903, "
    "at Kitty Hawk, North Carolina. The flight covered 120 feet and lasted 12 seconds. Orville Wright was "
    "the pilot for that first flight; Wilbur flew a later attempt the same day that covered 852 feet. "
    "Contrary to popular belief, the brothers were not trained engineers -- they ran a bicycle repair shop "
    "in Dayton, Ohio, and did not have a college education. Their systematic wind-tunnel testing, however, "
    "was not amateurish: they tested over 200 wing designs before settling on their final configuration.",

    "Photosynthesis converts light energy into chemical energy stored in glucose. The overall reaction can "
    "be summarized as 6 CO2 + 6 H2O + light energy -> C6H12O6 + 6 O2. This process is not limited to plants; "
    "algae and some bacteria also photosynthesize. The light-dependent reactions occur in the thylakoid "
    "membrane, while the light-independent reactions (the Calvin cycle) occur in the stroma. Roughly 1-2% "
    "of incident sunlight energy is ultimately converted into chemical energy under typical field conditions.",

    "The Berlin Wall fell on November 9, 1989, after 28 years of dividing East and West Berlin. It was not "
    "a single wall but a fortified barrier system nearly 155 kilometers long, including 302 watchtowers. "
    "At least 140 people are known to have died attempting to cross it. Reunification of Germany followed "
    "less than a year later, on October 3, 1990 -- a date that is now celebrated annually as German Unity Day.",

    "Quantum entanglement does not allow faster-than-light communication, despite common misconceptions. "
    "When two particles are entangled, measuring one instantly determines the state of the other regardless "
    "of distance, but no usable information can be transmitted this way because the outcome of the local "
    "measurement is random. Einstein famously called this 'spooky action at a distance' and was never fully "
    "comfortable with it, though experiments since the 1980s have repeatedly confirmed the effect.",

    "The Great Barrier Reef stretches approximately 2,300 kilometers off the coast of Queensland, Australia, "
    "and is composed of over 2,900 individual reefs. It is not a single organism but a vast ecosystem hosting "
    "roughly 1,500 species of fish. Between 2016 and 2020, the reef experienced three mass coral bleaching "
    "events, and studies suggest it has lost more than half of its coral cover since 1995.",

    "The printing press, introduced by Johannes Gutenberg around 1440, did not invent movable type -- that "
    "had already appeared in China and Korea centuries earlier. Gutenberg's contribution was an oil-based "
    "ink and a practical hand mould that made casting durable, reusable type economically viable in Europe. "
    "Within 50 years, printing presses had been established in over 200 European cities.",

    "Caffeine is the most widely consumed psychoactive substance in the world, but it is not classified as "
    "a controlled substance in most countries. An average cup of coffee contains between 80 and 100 "
    "milligrams of caffeine, while a shot of espresso contains roughly 63 milligrams. The half-life of "
    "caffeine in an adult human is typically 3 to 5 hours, though this can vary substantially with genetics.",

    "The Apollo 11 mission landed on the Moon on July 20, 1969. Neil Armstrong and Buzz Aldrin spent about "
    "21 hours and 36 minutes on the lunar surface, while Michael Collins remained in orbit and never set "
    "foot on the Moon. Contrary to some accounts, the landing was not fully automated -- Armstrong took "
    "manual control during the final descent after noticing the autopilot was targeting a boulder field.",

    "Antibiotic resistance does not develop within an individual bacterium during a single course of "
    "treatment in the way many people imagine; rather, resistant strains that already exist at low "
    "frequency are selected for and proliferate when susceptible bacteria are killed off. The World Health "
    "Organization has warned that, without coordinated action, common infections could once again become "
    "untreatable by 2050.",

    "The Amazon rainforest produces roughly 20% of the world's oxygen, though scientists note this figure "
    "is often misunderstood: the rainforest is not a net oxygen source for the planet overall, because "
    "decomposition and respiration by the same ecosystem consume nearly all the oxygen it generates. It "
    "does, however, store an estimated 76 billion tons of carbon and spans about 5.5 million square kilometers.",

    "The Titanic sank on April 15, 1912, after striking an iceberg approximately 375 miles south of "
    "Newfoundland. Of the estimated 2,224 people aboard, more than 1,500 did not survive. The ship was not "
    "carrying enough lifeboats for even half its passengers and crew -- only 20 lifeboats were aboard, "
    "with a capacity of 1,178 people, though several launched only partially filled.",

    "Vaccines do not contain a live, dangerous dose of the pathogen they protect against in the vast "
    "majority of cases; most modern vaccines use inactivated viruses, weakened live viruses, or a small "
    "protein fragment (such as an mRNA-encoded spike protein) to train the immune system. Smallpox, once "
    "responsible for an estimated 300 million deaths in the 20th century alone, was declared eradicated "
    "in 1980 following a global vaccination campaign.",
]

CODE = [
    (
        "python",
        """def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1  # not found
""",
    ),
    (
        "javascript",
        """async function fetchWithRetry(url, maxRetries = 3) {
  let lastError = null;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (err) {
      lastError = err;
      await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
    }
  }
  throw lastError;
}
""",
    ),
    (
        "go",
        """func Fibonacci(n int) int {
	if n < 2 {
		return n
	}
	a, b := 0, 1
	for i := 2; i <= n; i++ {
		a, b = b, a+b
	}
	return b
}
""",
    ),
    (
        "rust",
        """fn quicksort<T: Ord + Clone>(arr: &[T]) -> Vec<T> {
    if arr.len() <= 1 {
        return arr.to_vec();
    }
    let pivot = arr[arr.len() / 2].clone();
    let less: Vec<T> = arr.iter().filter(|x| **x < pivot).cloned().collect();
    let equal: Vec<T> = arr.iter().filter(|x| **x == pivot).cloned().collect();
    let greater: Vec<T> = arr.iter().filter(|x| **x > pivot).cloned().collect();
    [quicksort(&less), equal, quicksort(&greater)].concat()
}
""",
    ),
    (
        "sql",
        """SELECT customer_id, COUNT(*) AS order_count, SUM(total_cents) / 100.0 AS total_usd
FROM orders
WHERE created_at >= '2024-01-01' AND status != 'cancelled'
GROUP BY customer_id
HAVING COUNT(*) >= 3
ORDER BY total_usd DESC
LIMIT 50;
""",
    ),
    (
        "python",
        '''class LRUCache:
    """A fixed-capacity cache that evicts the least-recently-used item."""

    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._data = {}
        self._order = []

    def get(self, key):
        if key not in self._data:
            return None
        self._order.remove(key)
        self._order.append(key)
        return self._data[key]

    def put(self, key, value):
        if key in self._data:
            self._order.remove(key)
        elif len(self._data) >= self.capacity:
            oldest = self._order.pop(0)
            del self._data[oldest]
        self._data[key] = value
        self._order.append(key)
''',
    ),
    (
        "typescript",
        """interface RetryPolicy {
  maxAttempts: number;
  backoffMs: number;
}

function withRetryPolicy<T>(fn: () => Promise<T>, policy: RetryPolicy): Promise<T> {
  let attempt = 0;
  const run = async (): Promise<T> => {
    try {
      return await fn();
    } catch (err) {
      attempt += 1;
      if (attempt >= policy.maxAttempts) throw err;
      await new Promise((r) => setTimeout(r, policy.backoffMs * attempt));
      return run();
    }
  };
  return run();
}
""",
    ),
    (
        "bash",
        """#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/var/backups/db"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
RETENTION_DAYS=7

pg_dump mydb | gzip > "${BACKUP_DIR}/mydb_${TIMESTAMP}.sql.gz"
find "${BACKUP_DIR}" -name '*.sql.gz' -mtime +"${RETENTION_DAYS}" -delete
echo "Backup complete: ${TIMESTAMP}"
""",
    ),
    (
        "c",
        """int gcd(int a, int b) {
    while (b != 0) {
        int t = b;
        b = a % b;
        a = t;
    }
    return a;
}
""",
    ),
    (
        "java",
        """public class RateLimiter {
    private final int maxTokens;
    private double tokens;
    private long lastRefillMs;
    private final double refillPerMs;

    public RateLimiter(int maxTokens, double refillPerSecond) {
        this.maxTokens = maxTokens;
        this.tokens = maxTokens;
        this.refillPerMs = refillPerSecond / 1000.0;
        this.lastRefillMs = System.currentTimeMillis();
    }

    public synchronized boolean tryAcquire() {
        long now = System.currentTimeMillis();
        tokens = Math.min(maxTokens, tokens + (now - lastRefillMs) * refillPerMs);
        lastRefillMs = now;
        if (tokens >= 1.0) {
            tokens -= 1.0;
            return true;
        }
        return false;
    }
}
""",
    ),
]

HTML = [
    """<!DOCTYPE html>
<html>
<head><title>Q3 2024 Report</title></head>
<body>
  <h1>Quarterly Summary</h1>
  <p>Revenue grew by <strong>14.2%</strong> compared to Q2 2024, reaching $3.4 million.
     Customer churn did <em>not</em> improve, remaining flat at 4.1%.</p>
  <ul>
    <li>New signups: 1,204</li>
    <li>Active accounts: 8,930</li>
    <li>Support tickets: 412 (down from 501)</li>
  </ul>
</body>
</html>""",
    """<article>
  <header><h2>Release Notes -- v2.3.0</h2></header>
  <p>This release does not include the previously announced dark mode toggle;
     it has been deferred to v2.4.0, expected on 2024-11-15.</p>
  <table border="1">
    <tr><th>Component</th><th>Status</th></tr>
    <tr><td>API Gateway</td><td>Stable</td></tr>
    <tr><td>Auth Service</td><td>Beta</td></tr>
  </table>
</article>""",
    """<div class="alert alert-warning">
  <p><strong>Maintenance Notice:</strong> The service will be unavailable on
     2024-08-01 from 02:00 to 04:00 UTC. No data will be lost during this window.</p>
</div>
<footer>
  <p>&copy; 2024 Example Corp. Not affiliated with any third-party trademark holders.</p>
</footer>""",
    """<section id="pricing">
  <h3>Pricing Plans</h3>
  <div class="plan">
    <h4>Starter</h4>
    <p class="price">$0/mo</p>
    <p>Includes 3 projects. Does not include priority support.</p>
  </div>
  <div class="plan">
    <h4>Pro</h4>
    <p class="price">$29/mo</p>
    <p>Includes 50 projects and priority support.</p>
  </div>
</section>""",
    """<form action="/subscribe" method="post">
  <label for="email">Email (we will never share it):</label>
  <input type="email" id="email" name="email" required>
  <button type="submit">Subscribe</button>
</form>
<p class="disclaimer">You can unsubscribe at any time. We do not sell your data.</p>""",
    """<nav>
  <ul>
    <li><a href="/">Home</a></li>
    <li><a href="/docs">Docs</a></li>
    <li><a href="/pricing">Pricing</a></li>
  </ul>
</nav>
<main>
  <h1>Welcome</h1>
  <p>Our uptime over the last 90 days was 99.982%, not counting two scheduled
     maintenance windows on 2024-03-12 and 2024-06-04.</p>
</main>""",
    """<blockquote cite="https://example.com/interview">
  <p>"We are not planning to raise prices in 2025," said the spokesperson,
     "though the free tier's storage limit will drop from 5GB to 2GB on
     2025-01-01."</p>
</blockquote>""",
    """<table>
  <caption>Server Regions</caption>
  <thead><tr><th>Region</th><th>Latency (ms)</th><th>Available</th></tr></thead>
  <tbody>
    <tr><td>us-east-1</td><td>12</td><td>Yes</td></tr>
    <tr><td>eu-west-1</td><td>34</td><td>Yes</td></tr>
    <tr><td>ap-south-1</td><td>89</td><td>No</td></tr>
  </tbody>
</table>""",
]

TABLES = [
    """| Quarter | Revenue ($M) | YoY Growth |
|---------|-------------:|-----------:|
| Q1 2024 | 2.1          | 8.4%       |
| Q2 2024 | 2.6          | 11.2%      |
| Q3 2024 | 3.4          | 14.2%      |
| Q4 2024 | 3.1          | -8.8%      |

Note: Q4 revenue did not meet the internal forecast of $3.9M.""",
    """| Employee ID | Department | Start Date | Active |
|-------------|------------|------------|--------|
| E-1001      | Engineering| 2021-03-15 | Yes    |
| E-1002      | Sales      | 2019-11-02 | No     |
| E-1003      | Support    | 2022-07-30 | Yes    |
| E-1004      | Engineering| 2023-01-09 | Yes    |

E-1002 is no longer with the company as of 2023-06-01.""",
    """| Metric              | Baseline | After Optimization |
|---------------------|---------:|--------------------:|
| p50 latency (ms)     | 142      | 61                  |
| p95 latency (ms)     | 890      | 310                 |
| p99 latency (ms)     | 2100     | 740                 |
| Error rate           | 0.8%     | 0.3%                |

The optimization did not change the p99 latency by an order of magnitude,
contrary to the initial (incorrect) internal estimate.""",
    """| Species          | Population (2010) | Population (2024) | Trend |
|------------------|-------------------:|-------------------:|-------|
| Snow Leopard     | 4,080              | 4,678               | Up    |
| Amur Tiger       | 360                | 540                 | Up    |
| Vaquita          | 245                | 10                  | Down  |
| Javan Rhino      | 40                 | 76                  | Up    |

The vaquita population has not stabilized despite conservation efforts
since 2010.""",
    """| Test Case | Expected | Actual | Pass |
|-----------|----------|--------|------|
| TC-001    | 200      | 200    | Yes  |
| TC-002    | 404      | 500    | No   |
| TC-003    | true     | true   | Yes  |
| TC-004    | null     | null   | Yes  |

TC-002 does not currently pass; the fix is scheduled for the 2024-09-20 release.""",
    """| Country   | Capital     | Population (M) | Independence |
|-----------|-------------|----------------:|--------------|
| Kenya     | Nairobi     | 55.1             | 1963-12-12   |
| Peru      | Lima        | 34.4             | 1821-07-28   |
| Vietnam   | Hanoi       | 98.2             | 1945-09-02   |

None of these figures include diaspora populations living abroad.""",
    """| Sensor | Reading | Threshold | Alert |
|--------|--------:|----------:|-------|
| Temp-1 | 78.4    | 85.0      | No    |
| Temp-2 | 91.2    | 85.0      | Yes   |
| Pres-1 | 14.6    | 20.0      | No    |

Temp-2 has not returned below threshold since the alert triggered at 03:12 UTC.""",
    """| Model      | Accuracy | F1 Score | Params (M) |
|------------|---------:|---------:|-----------:|
| baseline   | 0.812    | 0.798    | 12         |
| v2         | 0.847    | 0.831    | 24         |
| v3 (final) | 0.891    | 0.879    | 24         |

v3 does not use more parameters than v2 -- the accuracy gain came entirely
from a change in the training data mix.""",
]

AGENT_TRANSCRIPTS = [
    "[Step 1] Tool: list_files(path='/repo/src')\nOutput: 42 files found: main.py, utils.py, config.py, "
    "... (39 more). No errors encountered.\n[Step 2] Tool: read_file(path='/repo/src/main.py')\n"
    "Output: 218 lines. The file does not currently have a main() guard; imports numpy, requests, and "
    "internal module `pipeline`.\n[Step 3] Agent: I will not modify main.py yet; first checking config.py.",

    "[Step 1] Tool: run_tests()\nOutput: 87 passed, 3 failed, 0 skipped. Failures: test_auth.py::test_expired_token, "
    "test_cache.py::test_eviction, test_cache.py::test_ttl.\n[Step 2] Agent: Two of three failures are in the cache "
    "module, which was not touched in this branch -- likely pre-existing flakiness, not caused by my change.\n"
    "[Step 3] Tool: git_blame(file='test_cache.py', line=45)\nOutput: last modified 2024-05-02, not related to "
    "current PR.",

    "[Step 1] Tool: search_codebase(query='rate limit')\nOutput: 6 matches across 4 files: rate_limiter.py (3), "
    "middleware.py (2), config.py (1).\n[Step 2] Tool: read_file(path='rate_limiter.py')\nOutput: 94 lines. "
    "Uses a token-bucket algorithm; default capacity is 100 tokens, refill rate 10/sec. Does not currently "
    "support per-user overrides.\n[Step 3] Agent: Adding a per-user override is not a breaking change since "
    "the default behavior is preserved.",

    "[Step 1] Tool: query_database(sql='SELECT COUNT(*) FROM users WHERE active=true')\nOutput: 14,822 active "
    "users.\n[Step 2] Tool: query_database(sql='SELECT COUNT(*) FROM users WHERE created_at > \\'2024-01-01\\'')\n"
    "Output: 3,401 users created since 2024-01-01.\n[Step 3] Agent: Growth is not evenly distributed across "
    "months; January and February 2024 account for 61% of new signups.",

    "[Step 1] Tool: deploy(env='staging')\nOutput: Deployment succeeded in 43 seconds. Version: v2.7.1-rc3. "
    "Health check: 200 OK.\n[Step 2] Tool: run_smoke_tests(env='staging')\nOutput: 18/18 passed.\n"
    "[Step 3] Agent: Staging looks healthy; I will not promote to production yet since the canary window "
    "is not complete until 2024-10-05 14:00 UTC.",

    "[Step 1] Tool: read_file(path='CHANGELOG.md')\nOutput: 340 lines, most recent entry dated 2024-09-30, "
    "version 4.2.0. Does not mention the security patch merged yesterday.\n[Step 2] Agent: I need to add an "
    "entry for the unreleased security fix before cutting the release.\n[Step 3] Tool: append_file(path="
    "'CHANGELOG.md', content='## Unreleased\\n- Fix: session tokens no longer leak in debug logs.')\n"
    "Output: write succeeded.",

    "[Step 1] Tool: run_shell(cmd='pip install -r requirements.txt')\nOutput: Successfully installed 23 packages. "
    "No conflicts detected.\n[Step 2] Tool: run_shell(cmd='python -m pytest')\nOutput: ImportError: cannot import "
    "name 'validate' from 'schema'. 0 tests collected.\n[Step 3] Agent: This is not a test failure, it's an "
    "import error -- likely a version mismatch introduced by the install in Step 1.",

    "[Step 1] Tool: fetch_url(url='https://status.example.com/api/incidents')\nOutput: 2 open incidents: "
    "INC-4471 (database latency, opened 2024-07-11 09:14 UTC, not yet resolved), INC-4472 (minor, resolved).\n"
    "[Step 2] Agent: INC-4471 has been open for over 6 hours; escalating is warranted since it does not show "
    "signs of auto-resolving.",

    "[Step 1] Tool: analyze_dependencies(package='left-pad')\nOutput: 0 direct dependents in this repo. Not "
    "used anywhere; safe to remove.\n[Step 2] Tool: remove_dependency(package='left-pad')\nOutput: removed "
    "from package.json and lockfile regenerated. Build did not break.\n[Step 3] Agent: Confirmed no regression; "
    "this cleanup is complete.",

    "[Step 1] Tool: check_disk_usage(path='/var/log')\nOutput: 18.4 GB used, 91% of quota. Largest file: "
    "app.log.3 at 6.2 GB.\n[Step 2] Tool: rotate_logs()\nOutput: rotated 5 files, freed 12.1 GB. Disk usage "
    "is now 34%, no longer near the alert threshold of 90%.\n[Step 3] Agent: Log rotation resolved the "
    "immediate issue; scheduling a cron job so this does not recur.",
]

ADVERSARIAL_NEGATIONS = [
    "The patch does not fix the memory leak; it only reduces its rate of growth, which is not the same "
    "thing as a fix and should not be reported as one.",

    "It is not true that the server never restarted last night -- it restarted twice, at 02:14 and 03:47 "
    "UTC, neither of which was scheduled.",

    "The contract states the vendor shall not be liable for indirect damages, but this does not mean the "
    "vendor bears no liability at all for direct damages, which remain uncapped.",

    "Nobody on the team said the migration was risk-free; what was said is that it was not expected to "
    "cause downtime, which turned out to be correct -- there was no downtime, though there was a brief "
    "period of degraded read latency that was not zero.",

    "The study did not find that the drug is ineffective; rather, it found no statistically significant "
    "difference at the tested dose, which is not the same conclusion as 'it doesn't work.'",

    "It would be incorrect to say the feature was never requested -- it was requested twice, but neither "
    "request was prioritized, so it was not built until this quarter.",

    "The audit did not uncover any fraud, but it did note that internal controls were not consistently "
    "followed, which is not evidence of wrongdoing yet is also not something that should be ignored.",
]


def build_corpus() -> list[dict]:
    items: list[dict] = []
    for i, text in enumerate(PROSE, start=1):
        items.append(payload(f"prose-{i:02d}", "prose", text))
    for i, (lang, code) in enumerate(CODE, start=1):
        items.append(payload(f"code-{i:02d}-{lang}", "code", code))
    for i, html in enumerate(HTML, start=1):
        items.append(payload(f"html-{i:02d}", "html", html))
    for i, table in enumerate(TABLES, start=1):
        items.append(payload(f"table-{i:02d}", "table", table))
    for i, transcript in enumerate(AGENT_TRANSCRIPTS, start=1):
        items.append(payload(f"agent-transcript-{i:02d}", "agent_transcript", transcript))
    for i, text in enumerate(ADVERSARIAL_NEGATIONS, start=1):
        items.append(payload(f"adversarial-negation-{i:02d}", "adversarial_negation", text))
    return items


def main() -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    items = build_corpus()
    for item in items:
        path = CORPUS_DIR / f"{item['id']}.json"
        path.write_text(json.dumps(item, indent=2) + "\n")
    print(f"Wrote {len(items)} corpus payloads to {CORPUS_DIR}")
    by_category: dict[str, int] = {}
    for item in items:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
