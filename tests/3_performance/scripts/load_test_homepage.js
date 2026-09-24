import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<400"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

export default function () {
  const res = http.get(`${BASE_URL}/system/health`);
  check(res, {
    "status is 200": (r) => r.status === 200,
  });
  sleep(0.5);
}
