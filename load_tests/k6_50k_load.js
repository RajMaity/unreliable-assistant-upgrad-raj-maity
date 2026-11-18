// k6 load script (design-only at 50K). Local: k6 run -e VUS=20 -e DURATION=30s load_tests/k6_50k_load.js
// Thresholds mirror design §1.5: p50 240 / p95 450 / p99 590 ms, 5xx ≤ 0.05%.
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.API_URL || "http://127.0.0.1:8000";
const VUS = parseInt(__ENV.VUS || "20", 10);
const DURATION = __ENV.DURATION || "30s";
const QUESTIONS = [
  "What is the safety protocol for LOT-88392?",
  "What is the storage condition for LOT-00044?",
  "How often is SKU-099 inspected?",
  "What is the returns policy?",
  "Which shift runs late?",
];

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_duration: ["p(50)<240", "p(95)<450", "p(99)<590"],
    http_req_failed: ["rate<0.0005"],
  },
};

export default function () {
  const body = JSON.stringify({
    tenant_id: "tenant_7",
    user_id: `user_${__VU % 50}`,
    session_id: "sess_ab12cd34ef",
    question: QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)],
    tier: "TIER_1_READ",
  });
  const res = http.post(`${BASE}/query`, body, { headers: { "Content-Type": "application/json" } });
  check(res, { "status is 200": (r) => r.status === 200 });
}
