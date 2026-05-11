# Stage 1 Flux queries

One `.flux` file per InfluxDB measurement listed in
[`docs/ANALYSIS_PIPELINE.md`](../../../docs/ANALYSIS_PIPELINE.md) §2.1.
The pipeline reads each file, substitutes `$bucket`, `$start`, `$end`,
runs the query, and writes the result to `stage1/<measurement>.parquet`.

These queries are part of the binding contract — they determine what
data the analysis sees. They are frozen at OSF lock.
