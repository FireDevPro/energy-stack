"""InfluxDB Flux wrapper for decision-trace report queries.

Construct via from_env() in production. Tests inject query_api directly.
No live Pi/LAN calls in tests.
"""
from typing import Any


class InfluxClient:
    """Wrapper around influxdb-client's query_api for hvac.* + feed health."""

    def __init__(self, url: str, token: str, org: str, bucket: str, query_api=None):
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        if query_api is None:
            # Production path — defer the import so tests don't require
            # the influxdb-client package on the test path.
            from influxdb_client import InfluxDBClient
            self._client = InfluxDBClient(url=url, token=token, org=org)
            self._query_api = self._client.query_api()
        else:
            self._client = None
            self._query_api = query_api

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "InfluxClient":
        return cls(
            url=env["INFLUXDB_URL"],
            token=env["INFLUXDB_TOKEN"],
            org=env["INFLUXDB_ORG"],
            bucket=env.get("INFLUXDB_BUCKET", "energy"),
        )

    def fetch_hvac_decisions(self, target_date_iso: str) -> list[dict[str, Any]]:
        """All hvac.decisions rows for `target_date_iso`."""
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: -7d)
              |> filter(fn: (r) => r._measurement == "hvac.decisions"
                                    and r.decision_for_date == "{target_date_iso}")
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._flatten_query(flux)

    def _flatten_query(self, flux: str) -> list[dict[str, Any]]:
        """Run a Flux query and flatten each record into a single dict."""
        out: list[dict[str, Any]] = []
        for table in self._query_api.query(flux):
            for record in table.records:
                row = dict(record.values)
                row["_time"] = record.get_time()
                out.append(row)
        return out
