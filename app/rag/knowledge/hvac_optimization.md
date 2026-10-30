# HVAC Optimization Guide

## Scheduling and occupancy
HVAC systems are usually the largest controllable load in offices and clinics. The first
and cheapest measure is to align the HVAC schedule in the Building Management System (BMS)
with real occupancy. Equipment running at night or during weekends in an unoccupied office
is pure waste. Typical causes are a manual override left active, a holiday calendar not
loaded into the BMS, or a time-zone / daylight-saving error in the controller.
Corrective action: audit the BMS schedules weekly, remove permanent overrides, and add an
automatic override expiry of 4 hours. Expected savings: 10 to 25 % of HVAC energy.

## Setpoints and deadband
Each 1 °C increase of the cooling setpoint saves roughly 6 to 8 % of cooling energy.
Recommended comfort setpoints are 24-26 °C in cooling mode and 20-21 °C in heating mode
with a deadband of at least 2 °C to avoid simultaneous heating and cooling.

## Night purge and free cooling
When the outdoor temperature at night is lower than the indoor temperature, ventilate the
building with outdoor air (night purge) before occupancy instead of running chillers.
Economizers on air handling units provide free cooling when outdoor air is cool and dry.

## Chiller faults and short cycling
A sudden spike of HVAC consumption lasting a few hours often indicates a chiller fault:
short cycling, low refrigerant charge, a failed condenser fan, or a stuck valve. Check
compressor run-time counters and alarms, inspect the condenser, and verify the chilled
water supply temperature. Short cycling also shortens equipment life.

## Maintenance
Clean or replace filters every 3 months, clean coils yearly, and verify sensor calibration.
Dirty filters increase fan power by 15 % or more.
