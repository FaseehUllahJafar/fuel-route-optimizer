import random

from django.test import SimpleTestCase

from routing.services.optimizer import NoFeasiblePlan, Station, plan_fuel_stops


def brute_force(stations, route_miles, capacity, first_stop_max, penalty):
    """Exhaustive search over whole-mile fuel levels, with mpg = 1 so fuel is in miles.

    Before the first stop the vehicle runs a fuel debt that must be paid off at
    that stop, mirroring the optimizer's "tank starts empty" model. Returns the
    (fuel cost, number of stops) of the plan minimizing cost + penalty * stops,
    or None when no plan exists.
    """
    # (fuel, has_stopped) -> (objective, fuel cost, stops); fuel < 0 is a debt.
    best = {(0, False): (0.0, 0.0, 0)}
    position = 0
    for station in sorted(stations, key=lambda s: s.mile):
        distance, position = station.mile - position, station.mile
        best = _drive(best, distance, allow_debt=station.mile <= first_stop_max)

        after_buying = dict(best)
        for (fuel, has_stopped), (objective, cost, stops) in best.items():
            min_buy = 1 if has_stopped else max(1, -fuel)
            max_buy = capacity - fuel  # a fuel debt (negative fuel) is extra room
            for buy in range(min_buy, max_buy + 1):
                candidate = (
                    objective + buy * station.price + penalty,
                    cost + buy * station.price,
                    stops + 1,
                )
                _keep_better(after_buying, (fuel + buy, True), candidate)
        best = after_buying

    arrived = _drive(best, route_miles - position, allow_debt=False)
    plans = [value for (fuel, has_stopped), value in arrived.items() if has_stopped]
    if not plans:
        return None
    _, cost, stops = min(plans)
    return cost, stops


def _drive(states, distance, allow_debt):
    moved = {}
    for (fuel, has_stopped), value in states.items():
        if has_stopped and fuel - distance < 0:
            continue  # ran dry
        if not has_stopped and not allow_debt:
            continue  # passed the first-stop window without stopping
        _keep_better(moved, (fuel - distance, has_stopped), value)
    return moved


def _keep_better(states, key, value):
    if key not in states or value[0] < states[key][0]:
        states[key] = value


class OptimizerTests(SimpleTestCase):
    def plan(self, stations, route, rng=500, mpg=10, first=50, penalty=0.0):
        return plan_fuel_stops(stations, route, rng, mpg, first, penalty)

    def test_total_gallons_cover_the_whole_trip(self):
        st = [Station(1, 5, 3.0), Station(2, 400, 3.5), Station(3, 800, 2.5)]
        plan = self.plan(st, 1000)
        self.assertAlmostEqual(plan.total_gallons, 100.0)

    def test_buys_just_enough_to_reach_cheaper_station(self):
        st = [Station(1, 0, 4.0), Station(2, 300, 3.0)]
        plan = self.plan(st, 600)
        self.assertEqual([p.station.key for p in plan.purchases], [1, 2])
        self.assertAlmostEqual(plan.purchases[0].gallons(10), 30.0)
        self.assertAlmostEqual(plan.purchases[1].gallons(10), 30.0)
        self.assertAlmostEqual(plan.total_cost, 30 * 4.0 + 30 * 3.0)

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        st = [Station(1, 0, 2.0), Station(2, 450, 5.0)]
        plan = self.plan(st, 700)
        self.assertAlmostEqual(plan.purchases[0].gallons(10), 50.0)
        self.assertAlmostEqual(plan.purchases[1].gallons(10), 20.0)

    def test_first_stop_fuel_is_charged_at_first_stop(self):
        st = [Station(1, 30, 3.0)]
        plan = self.plan(st, 300)
        self.assertEqual(len(plan.purchases), 1)
        self.assertAlmostEqual(plan.purchases[0].tank_gallons(10), 27.0)
        self.assertAlmostEqual(plan.purchases[0].start_fuel_miles, 30.0)
        self.assertAlmostEqual(plan.purchases[0].gallons(10), 30.0)
        self.assertAlmostEqual(plan.total_cost, 90.0)

    def test_tank_purchase_never_exceeds_capacity(self):
        # Arrive at mile 40 "empty" and fill up for a long expensive stretch.
        plan = self.plan([Station(1, 40, 2.0), Station(2, 500, 5.0)], 900)
        self.assertAlmostEqual(plan.purchases[0].tank_gallons(10), 50.0)
        self.assertAlmostEqual(plan.purchases[0].gallons(10), 54.0)

    def test_no_station_near_start(self):
        with self.assertRaisesRegex(NoFeasiblePlan, "start"):
            self.plan([Station(1, 120, 3.0)], 300)

    def test_gap_longer_than_range(self):
        with self.assertRaisesRegex(NoFeasiblePlan, "Gap"):
            self.plan([Station(1, 0, 3.0), Station(2, 100, 3.0)], 700)

    def test_stations_beyond_destination_ignored(self):
        plan = self.plan([Station(1, 0, 3.0), Station(2, 350, 1.0)], 200)
        self.assertEqual([p.station.key for p in plan.purchases], [1])

    def test_penalty_drops_trivial_stops(self):
        # A station a cent cheaper 10 miles on: worth a stop only when stops are free.
        # first=5 keeps station 2 out of the first-stop window.
        st = [Station(1, 0, 3.00), Station(2, 10, 2.99)]
        self.assertEqual(len(self.plan(st, 400, first=5, penalty=0).purchases), 2)
        self.assertEqual(len(self.plan(st, 400, first=5, penalty=1).purchases), 1)

    def test_matches_brute_force_on_random_instances(self):
        rnd = random.Random(42)
        checked = 0
        for _ in range(400):
            route = rnd.randint(15, 60)
            cap = rnd.randint(8, 20)
            first_max = rnd.randint(0, 8)
            miles = sorted(rnd.sample(range(0, route), rnd.randint(2, min(12, route))))
            st = [Station(i, m, float(rnd.randint(1, 9))) for i, m in enumerate(miles)]
            penalty = rnd.choice([0.0, 0.0, 2.0, 7.0])
            expected = brute_force(st, route, cap, first_max, penalty)
            if expected is None:
                with self.assertRaises(NoFeasiblePlan):
                    plan_fuel_stops(st, route, cap, 1.0, first_max, penalty)
                continue
            plan = plan_fuel_stops(st, route, cap, 1.0, first_max, penalty)
            exp_cost, exp_stops = expected
            self.assertAlmostEqual(
                plan.total_cost + penalty * len(plan.purchases),
                exp_cost + penalty * exp_stops,
                places=6,
                msg=f"{st=} {route=} {cap=} {first_max=} {penalty=}",
            )
            self.assertAlmostEqual(plan.total_gallons, route, places=6)
            self._assert_feasible(plan, route, cap)
            checked += 1
        self.assertGreater(checked, 150)

    def _assert_feasible(self, plan, route, cap):
        """Replay the plan: never run dry, never exceed the tank."""
        first = plan.purchases[0]
        self.assertAlmostEqual(first.start_fuel_miles, first.station.mile)
        fuel = first.miles_of_fuel
        self.assertLessEqual(fuel, cap + 1e-6)
        pos = first.station.mile
        for p in plan.purchases[1:]:
            fuel -= p.station.mile - pos
            self.assertGreaterEqual(fuel, -1e-6)
            self.assertAlmostEqual(fuel, p.fuel_on_arrival_miles, places=6)
            fuel += p.miles_of_fuel
            self.assertLessEqual(fuel, cap + 1e-6)
            pos = p.station.mile
        self.assertGreaterEqual(fuel - (route - pos), -1e-6)
