# G:\Learning\F1Data\F1Data_App\core\serializers.py
from rest_framework import serializers
from .models import Meetings, Sessions, Drivers, Weather, SessionResult, Laps, Pit, Stint, Position, Intervals, RaceControl, TeamRadio, CarData, Location, Circuit

from django.db.models import Model

# Serializer para listar anos distintos
class YearSerializer(serializers.Serializer):
    year = serializers.ListField(child=serializers.IntegerField())

# Serializer para listar meetings com o formato de filtro específico
class MeetingFilterSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    meeting_official_name = serializers.SerializerMethodField()

    class Meta:
        model = Meetings
        fields = ['meeting_key', 'year', 'country_name', 'meeting_name', 'circuit_short_name', 'circuit_key', 'meeting_official_name', 'display_name']

    def get_display_name(self, obj):
        if isinstance(obj, Model):
            country_name = obj.country_name
            meeting_name = obj.meeting_name
            circuit_short_name = obj.circuit_short_name
        else:
            country_name = obj.get('country_name', 'N/A')
            meeting_name = obj.get('meeting_name', 'N/A')
            circuit_short_name = obj.get('circuit_short_name', 'N/A')

        return f"{country_name} - {meeting_name} - {circuit_short_name}"

    def get_meeting_official_name(self, obj):
        if isinstance(obj, Model):
            return obj.meeting_official_name or obj.meeting_name
        else:
            return obj.get('meeting_official_name', obj.get('meeting_name', 'N/A'))

#Serializer para Meetings
class MeetingSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    meeting_official_name = serializers.SerializerMethodField()

    class Meta:
        model = Meetings
        fields = ['meeting_key', 'year', 'country_name', 'meeting_name', 'circuit_short_name', 'circuit_key', 'meeting_official_name', 'display_name']

    def get_display_name(self, obj):
        return f"{obj.country_name} - {obj.meeting_name} - {obj.circuit_short_name}"

    def get_meeting_official_name(self, obj):
        return obj.meeting_official_name or obj.meeting_name


# Serializer para sessões
class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sessions
        fields = ['session_key', 'date_start', 'session_name'] #, 'circuit_short_name']
        
# Serializer para Drivers
class DriversSerializer(serializers.ModelSerializer):
    class Meta:
        model = Drivers
        fields = ['driver_number', 'broadcast_name', 'full_name', 'name_acronym', 'team_name', 'team_colour', 'first_name', 'last_name', 'headshot_url', 'country_code']
        
# Serializer para Weather
class WeatherSerializer(serializers.ModelSerializer):
    class Meta:
        model = Weather
        fields = ['session_key', 'meeting_key', 'session_date', 'wind_direction', 'air_temperature', 'humidity', 'pressure', 'rainfall', 'wind_speed', 'track_temperature',]
        
# Serializer para SessionResult
class SessionResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionResult
        fields = ['position','driver_number','time_gap','number_of_laps','meeting_key','session_key'] 
        
# Serializer para Laps
class LapsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Laps
        fields = ['session_key', 'driver_number', 'lap_number', 'date_start', 'duration_sector_1', 'duration_sector_2',
                  'duration_sector_3', 'i1_speed', 'i2_speed', 'is_pit_out_lap', 'lap_duration', 'segments_sector_1',
                  'segments_sector_2', 'segments_sector_3', 'st_speed'] 
        
#Serializer para Pit Stops
class PitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Pit
        fields = ['session_key', 'driver_number', 'lap_number', 'date', 'pit_stop_duration']

#Serializer para Stints
class StintSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stint
        fields = ['session_key', 'driver_number', 'stint_number', 'lap_start', 'lap_end', 'compound', 'tyre_age_at_start']
        
#Serializer para Position
class PositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Position
        fields = ['session_key', 'driver_number', 'position', 'date']
        
#Serializer para Intervals
class IntervalsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Intervals
        fields = ['session_key', 'driver_number', 'date', 'gap_to_leader', 'interval']
        
# Serializer para RaceControl
class RaceControlSerializer(serializers.ModelSerializer):
    class Meta:
        model = RaceControl
        fields = ['session_key', 'session_date', 'driver_number', 'lap_number', 'category', 'flag', 'scope', 'sector', 'message']
        
#Serializer para TeamRadio
class TeamRadioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeamRadio
        fields = ['session_key', 'driver_number', 'date', 'recording_url']
        
# Serializer para CarData
class CarDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = CarData
        fields = ['session_key', 'driver_number', 'date', 'speed', 'n_gear', 'drs', 'throttle', 'break', 'rpm']
        
#Serializer para Location
class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['session_key', 'driver_number', 'date', 'z', 'x', 'y']
        
# Serializer para Circuit
class CircuitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Circuit
        fields = ['circuitid','circuitref','name','location','country','lat','lng','alt','url']


        