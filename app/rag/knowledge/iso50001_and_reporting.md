# Energy Management System (ISO 50001) and Reporting

## Plan-Do-Check-Act
ISO 50001 structures energy management as a continuous improvement cycle: establish an
energy policy and baseline (Plan), implement action plans (Do), monitor energy performance
indicators (Check), and review results with management (Act).

## Energy baseline and EnPIs
An energy baseline is the reference consumption used to measure improvements. Energy
performance indicators (EnPIs) include kWh per m², kWh per unit produced, PUE for data
centers, and specific consumption per bed for hospitals.

## Monitoring and anomaly detection
Sub-metering per usage (HVAC, lighting, process, compressed air) combined with automatic
anomaly detection against the baseline is the foundation of continuous monitoring.
Each anomaly should be assigned an owner, a root cause and a corrective action.

## Carbon reporting
Scope 2 emissions = grid electricity consumed x grid emission factor. The demo uses an
illustrative factor of 0.72 kg CO2/kWh. Renewable self-generation reduces scope 2.

## Human approval of actions
Actions that change equipment operation (stopping compressors, changing setpoints in a
hospital, modifying data-center cooling) must be approved by the site energy manager and
logged in an audit trail.
