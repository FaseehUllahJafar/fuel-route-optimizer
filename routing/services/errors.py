class TripPlanningError(Exception):
    """Base class for errors that should be reported to the API client."""


class GeocodingError(TripPlanningError):
    """The input could not be resolved to a location in the USA."""


class NoRouteError(TripPlanningError):
    """The routing service found no drivable route between the two points."""


class RoutingUnavailableError(TripPlanningError):
    """The routing service failed or could not be reached."""


class NoFeasiblePlan(TripPlanningError):
    """The route cannot be driven with the vehicle's range and the known stations."""
