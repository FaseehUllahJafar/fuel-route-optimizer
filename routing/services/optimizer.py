"""Minimum-cost refueling along a fixed route.

Model
-----
Stations sit at a mile marker along the route and sell fuel at a fixed price.
Tank capacity U is the vehicle range, measured in miles of fuel; consumption is
linear. The tank starts empty: the vehicle leaves with just enough fuel to reach
its first stop and pays for that fuel there, so every mile driven is paid for.
Each stop adds `stop_penalty` dollars to the objective (but not to the reported
cost); without it the optimum makes stops to buy half a gallon a cent cheaper.

Algorithm
---------
Khuller, Malekian & Mestre, "To Fill or not to Fill: The Gas Station Problem"
(ACM TALG, 2011). For a fixed set of stops, the optimal policy at stop u is to
buy just enough to reach the next stop if it is no dearer, otherwise fill up.
So the fuel on arrival at a stop is either 0 or U - d(w, u) for an earlier
stop w, which gives the recurrence

    C(u, g) = penalty + min(
        min over no-dearer v with d(u,v) > g:  c(u)·(d(u,v) - g) + C(v, 0)
        min over dearer v:                     c(u)·(U - g)      + C(v, U - d(u,v))
    )

with the destination as a free station. The first branch is a suffix minimum
over v sorted by distance and the second does not depend on g, so evaluating a
state is O(log W) and the whole plan O(n·W·log W), W being the number of
stations within one tank of range.
"""

import bisect
import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import NamedTuple

from .errors import NoFeasiblePlan

EPS = 1e-9


@dataclass(frozen=True)
class Station:
    key: int
    mile: float
    price: float  # USD per gallon


@dataclass
class Purchase:
    station: Station
    miles_of_fuel: float  # pumped into the tank, never more than its capacity
    fuel_on_arrival_miles: float
    start_fuel_miles: float = 0.0  # first stop only: fuel burnt getting there

    def tank_gallons(self, mpg: float) -> float:
        return self.miles_of_fuel / mpg

    def gallons(self, mpg: float) -> float:
        """All fuel paid for at this stop."""
        return (self.miles_of_fuel + self.start_fuel_miles) / mpg

    def cost(self, mpg: float) -> float:
        return self.gallons(mpg) * self.station.price


@dataclass
class FuelPlan:
    mpg: float
    purchases: list[Purchase] = field(default_factory=list)

    @property
    def total_gallons(self) -> float:
        return sum(p.gallons(self.mpg) for p in self.purchases)

    @property
    def total_cost(self) -> float:
        return sum(p.cost(self.mpg) for p in self.purchases)


def plan_fuel_stops(
    stations: list[Station],
    route_miles: float,
    range_miles: float,
    mpg: float,
    first_stop_max_miles: float,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    """Return the cheapest fuel plan; `stations` need not be sorted."""
    if range_miles <= 0 or mpg <= 0:
        raise ValueError("range_miles and mpg must be positive")
    if route_miles <= 0:
        return FuelPlan(mpg)

    ordered = _usable_stations(stations, route_miles)
    first_limit = min(first_stop_max_miles, range_miles)
    if not ordered or ordered[0].mile > first_limit + EPS:
        raise NoFeasiblePlan(
            f"No fuel station within {first_stop_max_miles:g} miles of the start of the route."
        )

    solver = _Solver(ordered, route_miles, range_miles, mpg, stop_penalty)
    best_cost, best_first = math.inf, None
    for u, station in enumerate(ordered):
        if station.mile > first_limit + EPS:
            break
        cost = solver.cost_per_mile[u] * station.mile + solver.cost(u, 0.0)
        if cost < best_cost - EPS:
            best_cost, best_first = cost, u
    if best_first is None:
        raise NoFeasiblePlan(_gap_message(ordered, route_miles, range_miles))
    return solver.reconstruct(best_first)


def _usable_stations(stations: list[Station], route_miles: float) -> list[Station]:
    """Drop stations past the destination; keep the cheapest of any sharing a mile marker."""
    cheapest_at: dict[float, Station] = {}
    for s in stations:
        if -EPS <= s.mile <= route_miles + EPS:
            current = cheapest_at.get(s.mile)
            if current is None or s.price < current.price:
                cheapest_at[s.mile] = s
    return sorted(cheapest_at.values(), key=lambda s: s.mile)


def _gap_message(ordered: list[Station], route_miles: float, range_miles: float) -> str:
    miles = [*(s.mile for s in ordered), route_miles]
    gap, after = max((b - a, a) for a, b in pairwise(miles))
    return (
        f"Gap of {gap:.0f} miles with no fuel station after mile {after:.0f}, "
        f"which exceeds the {range_miles:g}-mile range."
    )


class _Move(NamedTuple):
    cost: float
    next_stop: int  # index into the solver's stations; n means the destination
    bought: float  # miles of fuel pumped here
    arrival_fuel: float  # miles of fuel left on reaching next_stop


_NO_MOVE = _Move(math.inf, -1, 0.0, 0.0)


@dataclass
class _Transitions:
    """Moves out of one station, precomputed so each state is O(log W)."""

    cheap_distances: list[float]  # to no-dearer stations and the destination, ascending
    cheap_suffix_min: list[tuple[float, int]]  # (c(u)·d + C(v, 0), v), suffix minima
    dear_best: float = math.inf  # min over dearer v of C(v, U - d(u, v))
    dear_next: int = -1


class _Solver:
    def __init__(self, ordered, route_miles, range_miles, mpg, stop_penalty):
        self.stations = ordered
        self.n = len(ordered)
        self.mile = [s.mile for s in ordered] + [route_miles]  # index n is the destination
        self.cost_per_mile = [s.price / mpg for s in ordered]
        self.capacity = range_miles
        self.mpg = mpg
        self.penalty = stop_penalty

        # C(v, ·) only depends on stations after v, so fill the table back to front.
        self.transitions: list[_Transitions] = [None] * self.n
        self.cost_arriving_empty = [math.inf] * self.n
        for u in reversed(range(self.n)):
            self.transitions[u] = self._transitions_from(u)
            self.cost_arriving_empty[u] = self.cost(u, 0.0)

    def cost(self, u: int, arrival_fuel: float) -> float:
        """C(u, g): cheapest way to finish after stopping at u with g miles of fuel."""
        move = self._best_move(u, arrival_fuel)
        return move.cost + self.penalty if move.next_stop >= 0 else math.inf

    def reconstruct(self, first: int) -> FuelPlan:
        plan = FuelPlan(self.mpg)
        u, fuel = first, 0.0
        while u != self.n:
            move = self._best_move(u, fuel)
            if move.next_stop < 0:
                raise AssertionError("an optimal plan was found but cannot be reconstructed")
            plan.purchases.append(
                Purchase(
                    self.stations[u],
                    move.bought,
                    fuel_on_arrival_miles=fuel,
                    start_fuel_miles=self.mile[first] if u == first else 0.0,
                )
            )
            u, fuel = move.next_stop, move.arrival_fuel
        return plan

    def _reachable(self, u: int):
        limit = self.mile[u] + self.capacity + EPS
        v = u + 1
        while v <= self.n and self.mile[v] <= limit:
            yield v
            v += 1

    def _transitions_from(self, u: int) -> _Transitions:
        cheap = []
        best = _Transitions([], [])
        for v in self._reachable(u):
            d = self.mile[v] - self.mile[u]
            if v == self.n:
                cheap.append((d, self.cost_per_mile[u] * d, v))
            elif self.cost_per_mile[v] <= self.cost_per_mile[u]:
                cheap.append((d, self.cost_per_mile[u] * d + self.cost_arriving_empty[v], v))
            else:
                cost = self.cost(v, self.capacity - d)
                if cost < best.dear_best:
                    best.dear_best, best.dear_next = cost, v

        cheap.sort()
        suffix_min = [(math.inf, -1)] * len(cheap)
        running = (math.inf, -1)
        for k in reversed(range(len(cheap))):
            if cheap[k][1] < running[0]:
                running = (cheap[k][1], cheap[k][2])
            suffix_min[k] = running
        best.cheap_distances = [d for d, _, _ in cheap]
        best.cheap_suffix_min = suffix_min
        return best

    def _best_move(self, u: int, fuel: float) -> _Move:
        t = self.transitions[u]
        best = _NO_MOVE

        # Buy just enough to reach a no-dearer station we cannot already reach.
        k = bisect.bisect_right(t.cheap_distances, fuel + EPS)
        if k < len(t.cheap_distances):
            value, v = t.cheap_suffix_min[k]
            d = self.mile[v] - self.mile[u]
            best = _Move(value - fuel * self.cost_per_mile[u], v, d - fuel, 0.0)

        # Fill up and continue to a dearer station.
        if t.dear_next >= 0:
            cost = (self.capacity - fuel) * self.cost_per_mile[u] + t.dear_best
            if cost < best.cost - EPS:
                v = t.dear_next
                arrival = self.capacity - (self.mile[v] - self.mile[u])
                best = _Move(cost, v, self.capacity - fuel, arrival)
        return best
