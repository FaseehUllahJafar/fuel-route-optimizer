from django.db import models


class FuelStation(models.Model):
    """A truck stop from the OPIS price file, placed at its city's coordinates."""

    class GeocodeSource(models.TextChoices):
        GAZETTEER = "gazetteer", "Census Gazetteer"
        OVERRIDE = "override", "Manual override"

    opis_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    price = models.DecimalField(
        max_digits=7, decimal_places=4, help_text="Mean retail price per gallon, USD"
    )
    price_samples = models.PositiveSmallIntegerField(
        default=1, help_text="Rows in the price file this price was averaged over"
    )
    lat = models.FloatField()
    lon = models.FloatField()
    geocode_source = models.CharField(max_length=20, choices=GeocodeSource.choices)

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state})"
