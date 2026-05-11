# Expected Output — ComEd 2025 Threshold Analysis

Captured 2026-05-11 from the frozen `data/` files (5 monthly ComEd RTP exports + open-meteo ERA5 weather at 41.6151/-88.2018, snapped to ERA5 grid 41.58/-88.18).

Any change to the scripts or `data/` must reproduce these numbers; deviations indicate either a data refresh or a script change and must be reconciled before the change is merged.

---

## `analyze.py`

```
may2025.txt: 8882 5-min records, 2025-05-01 to 2025-05-31
jun2025.txt: 8621 5-min records, 2025-06-01 to 2025-06-30
jul2025.txt: 8817 5-min records, 2025-07-01 to 2025-07-31
aug2025.txt: 8892 5-min records, 2025-08-01 to 2025-08-31
sep2025.txt: 8597 5-min records, 2025-09-01 to 2025-09-30

Total 5-min records: 43809
Total hourly records: 3663
Date range: 2025-05-01 to 2025-09-30

=== Overall hourly price distribution (May-Sep 2025) ===
  Min:    -3.74
  P5:     1.52
  P25:    2.27
  P50:    3.17
  Mean:   4.30
  P75:    4.85
  P90:    7.10
  P95:    9.53
  P99:    20.47
  Max:    161.29

=== Hours above thresholds across all 5 months ===
  >=   5 c/kWh:   874 hours (23.86%)
  >=   8 c/kWh:   267 hours ( 7.29%)
  >=  10 c/kWh:   157 hours ( 4.29%)
  >=  12 c/kWh:   108 hours ( 2.95%)
  >=  15 c/kWh:    63 hours ( 1.72%)
  >=  20 c/kWh:    38 hours ( 1.04%)
  >=  25 c/kWh:    24 hours ( 0.66%)
  >=  30 c/kWh:    15 hours ( 0.41%)
  >=  50 c/kWh:     7 hours ( 0.19%)
  >= 100 c/kWh:     4 hours ( 0.11%)

=== Top 20 hours by hourly average price ===
   1. 2025-06-24 Tue 17:00 CT  161.29c/kWh
   2. 2025-06-24 Tue 18:00 CT  146.29c/kWh
   3. 2025-06-23 Mon 19:00 CT  135.82c/kWh
   4. 2025-06-23 Mon 18:00 CT  130.01c/kWh
   5. 2025-06-22 Sun 18:00 CT   75.08c/kWh
   6. 2025-06-23 Mon 17:00 CT   74.43c/kWh
   7. 2025-07-28 Mon 18:00 CT   57.33c/kWh
   8. 2025-06-24 Tue 16:00 CT   44.29c/kWh
   9. 2025-06-24 Tue 15:00 CT   41.45c/kWh
  10. 2025-06-24 Tue 19:00 CT   40.84c/kWh
  11. 2025-06-25 Wed 16:00 CT   39.38c/kWh
  12. 2025-08-15 Fri 17:00 CT   34.77c/kWh
  13. 2025-07-29 Tue 17:00 CT   31.65c/kWh
  14. 2025-06-24 Tue 12:00 CT   31.35c/kWh
  15. 2025-08-14 Thu 16:00 CT   30.06c/kWh
  16. 2025-06-30 Mon 11:00 CT   29.05c/kWh
  17. 2025-06-24 Tue 10:00 CT   28.38c/kWh
  18. 2025-07-15 Tue 16:00 CT   27.59c/kWh
  19. 2025-07-15 Tue 17:00 CT   27.32c/kWh
  20. 2025-09-04 Thu 19:00 CT   26.03c/kWh

=== Mean hourly price by hour-of-day (Jun-Sep 2025 summer) ===
  00:00 CT: mean= 2.73  p50= 2.61  p95= 4.74  max=  8.18  n=122
  01:00 CT: mean= 2.35  p50= 2.35  p95= 3.66  max=  4.22  n=122
  02:00 CT: mean= 2.14  p50= 2.15  p95= 2.92  max=  3.27  n=122
  03:00 CT: mean= 2.18  p50= 2.19  p95= 3.05  max=  3.66  n=122
  04:00 CT: mean= 2.52  p50= 2.41  p95= 3.94  max=  7.50  n=122
  05:00 CT: mean= 3.03  p50= 2.73  p95= 5.25  max= 12.53  n=121
  06:00 CT: mean= 3.45  p50= 2.98  p95= 7.77  max= 22.53  n=122
  07:00 CT: mean= 3.07  p50= 2.77  p95= 5.96  max= 11.84  n=122
  08:00 CT: mean= 3.22  p50= 2.95  p95= 6.07  max=  9.07  n=122
  09:00 CT: mean= 3.33  p50= 3.13  p95= 5.97  max=  8.88  n=122
  10:00 CT: mean= 4.38  p50= 3.59  p95= 9.81  max= 28.38  n=122
  11:00 CT: mean= 5.20  p50= 4.14  p95=13.65  max= 29.05  n=122
  12:00 CT: mean= 5.27  p50= 4.34  p95= 9.88  max= 31.35  n=122
  13:00 CT: mean= 5.22  p50= 4.45  p95=12.02  max= 18.22  n=122
  14:00 CT: mean= 5.31  p50= 4.75  p95=11.81  max= 23.97  n=118
  15:00 CT: mean= 5.74  p50= 5.20  p95=14.50  max= 41.45  n=120
  16:00 CT: mean= 6.87  p50= 5.47  p95=19.97  max= 44.29  n=122
  17:00 CT: mean= 9.22  p50= 6.08  p95=24.39  max=161.29  n=122
  18:00 CT: mean=11.03  p50= 6.61  p95=24.93  max=146.29  n=122
  19:00 CT: mean= 7.98  p50= 5.60  p95=14.65  max=135.82  n=122
  20:00 CT: mean= 5.35  p50= 4.84  p95=10.16  max= 16.27  n=121
  21:00 CT: mean= 4.68  p50= 4.12  p95= 8.51  max= 19.49  n=121
  22:00 CT: mean= 3.94  p50= 3.42  p95= 8.02  max= 14.57  n=122
  23:00 CT: mean= 3.13  p50= 2.78  p95= 6.44  max= 12.77  n=122

=== Hours above thresholds by hour-of-day (Jun-Sep 2025 summer) ===
  hour: >=10c   >=20c   >=50c
  00:00:   0h (  0.0%)    0h      0h
  01:00:   0h (  0.0%)    0h      0h
  02:00:   0h (  0.0%)    0h      0h
  03:00:   0h (  0.0%)    0h      0h
  04:00:   0h (  0.0%)    0h      0h
  05:00:   1h (  0.8%)    0h      0h
  06:00:   4h (  3.3%)    1h      0h
  07:00:   2h (  1.6%)    0h      0h
  08:00:   0h (  0.0%)    0h      0h
  09:00:   0h (  0.0%)    0h      0h
  10:00:   4h (  3.3%)    1h      0h
  11:00:  10h (  8.2%)    2h      0h
  12:00:   5h (  4.1%)    2h      0h
  13:00:  14h ( 11.5%)    0h      0h
  14:00:   8h (  6.8%)    2h      0h
  15:00:   9h (  7.5%)    1h      0h
  16:00:  15h ( 12.3%)    6h      0h
  17:00:  20h ( 16.4%)    9h      2h
  18:00:  29h ( 23.8%)    9h      4h
  19:00:  17h ( 13.9%)    5h      1h
  20:00:   6h (  5.0%)    0h      0h
  21:00:   3h (  2.5%)    0h      0h
  22:00:   3h (  2.5%)    0h      0h
  23:00:   1h (  0.8%)    0h      0h

=== Day-of-week patterns (Jun-Sep 2025 summer) ===
  Mon: mean= 5.55  p95=13.99  >=10c  40h  >=20c 11h
  Tue: mean= 6.04  p95=16.04  >=10c  40h  >=20c 14h
  Wed: mean= 4.77  p95=11.58  >=10c  24h  >=20c  5h
  Thu: mean= 4.52  p95= 9.78  >=10c  18h  >=20c  4h
  Fri: mean= 4.04  p95= 8.87  >=10c  11h  >=20c  2h
  Sat: mean= 3.67  p95= 7.67  >=10c   4h  >=20c  0h
  Sun: mean= 3.80  p95= 8.52  >=10c  14h  >=20c  2h

=== Scarcity event days (any hour >= 25 c/kWh, Jun-Sep) ===
  2025-06-22 (1h scarcity, max 75.1c, hours: [18])
  2025-06-23 (3h scarcity, max 135.8c, hours: [17, 18, 19])
  2025-06-24 (7h scarcity, max 161.3c, hours: [10, 12, 15, 16, 17, 18, 19])
  2025-06-25 (1h scarcity, max 39.4c, hours: [16])
  2025-06-30 (1h scarcity, max 29.1c, hours: [11])
  2025-07-15 (3h scarcity, max 27.6c, hours: [16, 17, 18])
  2025-07-28 (2h scarcity, max 57.3c, hours: [17, 18])
  2025-07-29 (2h scarcity, max 31.7c, hours: [17, 18])
  2025-08-14 (1h scarcity, max 30.1c, hours: [16])
  2025-08-15 (1h scarcity, max 34.8c, hours: [17])
  2025-09-04 (1h scarcity, max 26.0c, hours: [19])
  2025-09-19 (1h scarcity, max 25.8c, hours: [12])

=== Current 14-17 CT shutoff window analysis (Jun-Sep 2025) ===
  In-window (14-17 CT):  mean= 6.80  p95=17.38  max=161.29
  Out-of-window:          mean= 4.21  p95= 9.42  max=146.29
  Top 10 high-price hours OUTSIDE 14-17 CT window:
    2025-06-24 Tue 18:00  146.29c/kWh
    2025-06-23 Mon 19:00  135.82c/kWh
    2025-06-23 Mon 18:00  130.01c/kWh
    2025-06-22 Sun 18:00  75.08c/kWh
    2025-07-28 Mon 18:00  57.33c/kWh
    2025-06-24 Tue 19:00  40.84c/kWh
    2025-06-24 Tue 12:00  31.35c/kWh
    2025-06-30 Mon 11:00  29.05c/kWh
    2025-06-24 Tue 10:00  28.38c/kWh
    2025-09-04 Thu 19:00  26.03c/kWh

=== Negative pricing hours (any hour < 0 c/kWh) ===
  Total: 20 hours below 0 c/kWh (0.55% of all hours)
  Min price: -3.74 c/kWh
  Top hours-of-day with negative pricing:
    02:00 CT: 3 hours
    04:00 CT: 3 hours
    15:00 CT: 2 hours
    03:00 CT: 2 hours
    16:00 CT: 2 hours
```

---

## `correlate.py`

```
Joined records: 3663 hours (May-Sep 2025)

=== Pearson and Spearman correlations: hourly price vs weather variables (Jun-Sep 2025) ===
  temp_f      : Pearson r = +0.328   Spearman rho = +0.633
  apparent_f  : Pearson r = +0.317   Spearman rho = +0.631
  dewpoint_f  : Pearson r = +0.205   Spearman rho = +0.436
  rh_pct      : Pearson r = -0.152   Spearman rho = -0.248
  wind_mph    : Pearson r = +0.040   Spearman rho = +0.050
  solar_wm2   : Pearson r = +0.164   Spearman rho = +0.403

=== Weather conditions during price tiers (Jun-Sep 2025) ===
  Tier                    n      mean_temp  p95_temp  mean_dewp  mean_app   mean_solar
  Normal (<10c)            2768      71.6     86.0      61.8     74.0      238.4
  Elevated (10-20c)         113      81.0     90.6      67.7     86.1      404.7
  Scarcity (>=20c)           38      84.4     92.8      69.0     89.9      461.3

=== Daily max temp vs daily price spike count (Jun-Sep 2025) ===
  Daily max temp bin    n_days   any_spike(>=10c)  mult_spikes(>=3h>=10c)  scarcity(>=20c)  spike_count_avg
  60-70F                   6      1 ( 16.7%)        0 (  0.0%)           1 ( 16.7%)        0.2
  70-80F                  41     10 ( 24.4%)        3 (  7.3%)           2 (  4.9%)        0.5
  80-85F                  35     16 ( 45.7%)        4 ( 11.4%)           2 (  5.7%)        0.9
  85-90F                  32     19 ( 59.4%)        9 ( 28.1%)           9 ( 28.1%)        2.0
  90-95F                   8      8 (100.0%)        3 ( 37.5%)           3 ( 37.5%)        4.1

=== Days with scarcity events (>=20c) vs daily weather (Jun-Sep) ===

  Scarcity days (n=17):
    max_temp: mean=84.8F  range 65-93F
    max_dewp: mean=69.7F  range 50-78F
    max_app:  mean=90.2F

  Non-scarcity days (n=105):
    max_temp: mean=80.8F  range 65-92F
    max_dewp: mean=65.4F  range 40-78F

  Individual scarcity days:
    2025-06-24 Tue  max_temp=93F  max_dewp=74F  max_app=101F  max_price=161.3c  spikes=14h
    2025-06-23 Mon  max_temp=93F  max_dewp=73F  max_app=98F  max_price=135.8c  spikes=10h
    2025-06-22 Sun  max_temp=92F  max_dewp=74F  max_app=98F  max_price=75.1c  spikes=4h
    2025-07-28 Mon  max_temp=87F  max_dewp=78F  max_app=101F  max_price=57.3c  spikes=9h
    2025-06-25 Wed  max_temp=89F  max_dewp=74F  max_app=99F  max_price=39.4c  spikes=7h
    2025-08-15 Fri  max_temp=88F  max_dewp=73F  max_app=96F  max_price=34.8c  spikes=6h
    2025-07-29 Tue  max_temp=89F  max_dewp=78F  max_app=98F  max_price=31.7c  spikes=8h
    2025-08-14 Thu  max_temp=83F  max_dewp=67F  max_app=89F  max_price=30.1c  spikes=2h
    2025-06-30 Mon  max_temp=88F  max_dewp=72F  max_app=93F  max_price=29.1c  spikes=4h
    2025-07-15 Tue  max_temp=88F  max_dewp=73F  max_app=93F  max_price=27.6c  spikes=3h
    2025-09-04 Thu  max_temp=65F  max_dewp=50F  max_app=61F  max_price=26.0c  spikes=1h
    2025-09-19 Fri  max_temp=84F  max_dewp=67F  max_app=87F  max_price=25.8c  spikes=3h
    2025-09-25 Thu  max_temp=74F  max_dewp=59F  max_app=73F  max_price=23.8c  spikes=2h
    2025-09-24 Wed  max_temp=72F  max_dewp=61F  max_app=73F  max_price=22.5c  spikes=4h
    2025-09-29 Mon  max_temp=85F  max_dewp=64F  max_app=89F  max_price=21.0c  spikes=2h
    2025-08-06 Wed  max_temp=86F  max_dewp=69F  max_app=90F  max_price=20.5c  spikes=3h
    2025-08-18 Mon  max_temp=86F  max_dewp=76F  max_app=94F  max_price=20.4c  spikes=7h

=== Hot days (>=90F max) that did NOT have any price spike (>=10c) ===
  Count: 0

=== Mild days (<85F max) that DID have a price spike (>=10c) ===
  Count: 27
    [first 10 of 27 listed in console output, see q_under87.py full list]
```

---

## `q_humid.py`

```
=== Hot days (max temp >= 85F) split by max dewpoint ===
  Total hot days (>=85F max): 40
  Hot + dry (max dewp <60F)              1     0 (  0.0%)    0 (  0.0%)     0 (  0.0%)     8.7c
  Hot + moderate (60-67F dewp)           5     3 ( 60.0%)    1 ( 20.0%)     1 ( 20.0%)    11.8c
  Hot + humid (67-72F dewp)             10     6 ( 60.0%)    2 ( 20.0%)     2 ( 20.0%)    12.6c
  Hot + very humid (>=72F dewp)         24    18 ( 75.0%)    9 ( 37.5%)     9 ( 37.5%)    30.9c

=== Hot days split by max apparent temperature (heat index) ===
  Hot, HI < 90F (no heat-index uplift)      14    10 ( 71.4%)    3 ( 21.4%)     3 ( 21.4%)    13.3c
  Hot, HI 90-95F                            11     4 ( 36.4%)    2 ( 18.2%)     2 ( 18.2%)    12.7c
  Hot, HI 95-100F                           11     9 ( 81.8%)    5 ( 45.5%)     5 ( 45.5%)    41.1c
  Hot, HI >=100F                             4     4 (100.0%)    2 ( 50.0%)     2 ( 50.0%)    39.5c

=== Days at exactly 88-92F max temp split by humidity ===
  Total 88-92F days: 18
  Mid dewp (65-72F)           5   spike  3 ( 60.0%)   scarcity  0 (  0.0%)   avg_max_price  11.3c
  High dewp (>=72F)          13   spike  8 ( 61.5%)   scarcity  4 ( 30.8%)   avg_max_price  20.8c

=== Spike rate by temp x dewpoint matrix (Jun-Sep 2025) ===
                       low_dewp(<60F)   mid_dewp(60-67F)  high_dewp(67-72F)  vhi_dewp(>=72F)
  Temp 70-80F:   2/17=  12%        5/18=  28%        2/5=  40%        1/1= 100%
  Temp 80-85F:   0/1=   0%        7/13=  54%        6/13=  46%        3/8=  38%
  Temp 85-90F:   0/1=   0%        3/5=  60%        4/8=  50%        12/18=  67%
  Temp 90-100F:                n=0               n=0  2/2= 100%        6/6= 100%
```

---

## `q_under87.py`

```
Total summer days (Jun-Sep 2025): 122
Days with any spike (>=10c):      54
Days with scarcity event (>=20c): 17

=== Spike days (>=10c) by max-temp threshold ===
  Max temp < 82F:  14 of 54 spike days (25.9%)
  Max temp < 85F:  27 of 54 spike days (50.0%)
  Max temp < 87F:  36 of 54 spike days (66.7%)
  Max temp < 90F:  46 of 54 spike days (85.2%)
  Max temp < 92F:  51 of 54 spike days (94.4%)
  Max temp < 95F:  54 of 54 spike days (100.0%)

=== Scarcity days (>=20c) by max-temp threshold ===
  Max temp < 82F:   3 of 17 scarcity days (17.6%)
  Max temp < 85F:   5 of 17 scarcity days (29.4%)
  Max temp < 87F:   8 of 17 scarcity days (47.1%)
  Max temp < 90F:  14 of 17 scarcity days (82.4%)
  Max temp < 92F:  15 of 17 scarcity days (88.2%)
  Max temp < 95F:  17 of 17 scarcity days (100.0%)

=== Apparent-temp threshold check ===
  Max apparent < 82F:   8 of 54 spike days (14.8%)
  Max apparent < 85F:  14 of 54 spike days (25.9%)
  Max apparent < 87F:  18 of 54 spike days (33.3%)
  Max apparent < 90F:  30 of 54 spike days (55.6%)
  Max apparent < 92F:  32 of 54 spike days (59.3%)
  Max apparent < 95F:  40 of 54 spike days (74.1%)
  Max apparent < 100F:  52 of 54 spike days (96.3%)
```
