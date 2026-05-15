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

    def fetch_precool_window(self, target_date_iso: str) -> dict[str, Any] | None:
        """The hvac.precool_window row for `target_date_iso`, or None."""
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: -7d)
              |> filter(fn: (r) => r._measurement == "hvac.precool_window"
                                    and r.target_date == "{target_date_iso}")
              |> last()
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        rows = self._flatten_query(flux)
        return rows[0] if rows else None

    def fetch_hvac_actions_by_range(
        self,
        start_ct: "datetime",
        end_ct: "datetime",
    ) -> list[dict[str, Any]]:
        """Range-mode primitive: all `hvac.actions` rows in the CT
        datetime window `[start_ct, end_ct)`.

        `start_ct` / `end_ct` MUST be tz-aware datetimes in
        `America/Chicago` (or any equivalent zone). ZoneInfo handles
        CDT/CST symmetrically — DST is not encoded as a constant offset.
        """
        from datetime import timezone
        start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: {start_utc}, stop: {end_utc})
              |> filter(fn: (r) => r._measurement == "hvac.actions")
              |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
        """
        return self._flatten_query(flux)

    def fetch_hvac_actions(self, target_date_iso: str) -> list[dict[str, Any]]:
        """Date-mode convenience: all `hvac.actions` rows for the
        CT day `target_date_iso`. Delegates to
        `fetch_hvac_actions_by_range` after computing the day bounds.

        Both date-mode and range-mode flow through the same primitive
        so they can't drift out of sync.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        start_ct = datetime.fromisoformat(target_date_iso).replace(tzinfo=ct)
        end_ct = start_ct + timedelta(days=1)
        return self.fetch_hvac_actions_by_range(start_ct=start_ct, end_ct=end_ct)

    def fetch_comed_prices_above_by_range(
        self,
        start_ct: "datetime",
        end_ct: "datetime",
        threshold_cents: float,
    ) -> list[dict[str, Any]]:
        """Range-mode primitive: ComEd 5-min prices in CT window
        `[start_ct, end_ct)` with value >= `threshold_cents`. Field
        name is `price_cents_per_kwh`."""
        from datetime import timezone
        start_utc = start_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_utc = end_ct.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f"""
            from(bucket: "{self.bucket}")
              |> range(start: {start_utc}, stop: {end_utc})
              |> filter(fn: (r) => r._measurement == "comed.prices"
                                    and r._field == "price_cents_per_kwh"
                                    and r._value >= {threshold_cents})
        """
        return self._flatten_query(flux)

    def fetch_comed_prices_above(
        self,
        target_date_iso: str,
        threshold_cents: float,
    ) -> list[dict[str, Any]]:
        """Date-mode convenience. Delegates to the range primitive
        after computing CT-day bounds."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        start_ct = datetime.fromisoformat(target_date_iso).replace(tzinfo=ct)
        end_ct = start_ct + timedelta(days=1)
        return self.fetch_comed_prices_above_by_range(
            start_ct=start_ct, end_ct=end_ct, threshold_cents=threshold_cents,
        )
