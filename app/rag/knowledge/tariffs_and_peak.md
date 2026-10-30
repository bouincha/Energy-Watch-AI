# Tariffs, Peak Demand and Load Shifting

## Time-of-use tariffs
The EnergyWatch demo uses an illustrative time-of-use tariff with three periods:
peak (18:00-22:00) at 1.45 MAD/kWh, standard (07:00-18:00) at 1.05 MAD/kWh and off-peak
(22:00-07:00) at 0.75 MAD/kWh. Real tariffs depend on the utility contract and must be
loaded from the site's electricity contract.

## Load shifting
Move flexible loads out of the peak period: pre-cool buildings before 18:00, run
batch processes, water heating and EV charging off-peak, and use thermal storage.
Shifting 1 kWh from peak to off-peak saves 0.70 MAD with the demo tariff.

## Peak demand
Many contracts include a demand charge based on the maximum power. Stagger the start-up of
large motors and chillers, and use a load-shedding controller to cap the demand.

## Solar self-consumption
Rooftop photovoltaic production is most valuable when consumed on site. Schedule
flexible loads during sunny hours (10:00-15:00) to maximize self-consumption.
