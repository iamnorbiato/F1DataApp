# G:\Learning\F1Data\F1Data_App\core\serializers.py
from rest_framework import serializers
from .models import (
    Meetings, Sessions, Drivers, RaceControl, TeamRadio,
    CarData, Location, Intervals, Laps, Stint, Position,
    SessionResult, StartingGrid
)

class MeetingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meetings
        fields = '__all__' # Inclui todos os campos do modelo Meetings

class SessionSerializer(serializers.ModelSerializer):
    # Para DurationField (gmt_offset), o DRF por padrão o serializa como string (ex: "HH:MM:SS").
    # Se precisar de um formato específico, pode ser necessário um campo customizado.
    class Meta:
        model = Sessions
        fields = '__all__'

class DriverSerializer(serializers.ModelSerializer):
    class Meta:
        model = Drivers
        fields = '__all__'

class RaceControlSerializer(serializers.ModelSerializer):
    # session_date já é DateTimeField, será serializado normalmente
    class Meta:
        model = RaceControl
        fields = '__all__'

class TeamRadioSerializer(serializers.ModelSerializer):
    # date é DateTimeField
    class Meta:
        model = TeamRadio
        fields = '__all__'

class WeatherSerializer(serializers.ModelSerializer):
    # session_date é DateTimeField
    class Meta:
        model = Weather
        fields = '__all__'

class CarDataSerializer(serializers.ModelSerializer):
    # date é DateTimeField
    class Meta:
        model = CarData
        fields = '__all__'

class LocationSerializer(serializers.ModelSerializer):
    # date é DateTimeField
    class Meta:
        model = Location
        fields = '__all__'

class IntervalsSerializer(serializers.ModelSerializer):
    # date é DateTimeField, interval_value é DecimalField
    class Meta:
        model = Intervals
        fields = '__all__'

class LapsSerializer(serializers.ModelSerializer):
    # date_start é DateTimeField, campos duration_sector_X e lap_duration são DecimalField
    # segments_sector_X são ArrayField, que o DRF serializa como lista Python por padrão
    class Meta:
        model = Laps
        fields = '__all__'

class StintSerializer(serializers.ModelSerializer):
    # lap_start, lap_end, tyre_age_at_start são IntegerField, compound é CharField
    class Meta:
        model = Stint
        fields = '__all__'

class PositionSerializer(serializers.ModelSerializer):
    # date é DateTimeField, position é IntegerField
    class Meta:
        model = Position
        fields = '__all__'

class SessionResultSerializer(serializers.ModelSerializer):
    # position e number_of_laps são IntegerField, time_gap é CharField
    class Meta:
        model = SessionResult
        fields = '__all__'

class StartingGridSerializer(serializers.ModelSerializer):
    # position e driver_number são IntegerField, lap_duration é FloatField
    class Meta:
        model = StartingGrid
        fields = '__all__'