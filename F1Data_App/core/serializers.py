# G:\Learning\F1Data\F1Data_App\core\serializers.py
from rest_framework import serializers
from .models import Meetings, Sessions
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
        fields = ['meeting_key', 'year', 'country_name', 'meeting_name', 'circuit_short_name', 'meeting_official_name', 'display_name']

    def get_display_name(self, obj):
        if isinstance(obj, Model):
            country_name = obj.country_name
            meeting_name = obj.meeting_name
            circuit_short_name = obj.circuit_short_namea
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

# Serializer para sessões
class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sessions
        # Campos específicos que você pediu, mais session_key
        fields = ['session_key', 'date_start', 'session_name', 'circuit_short_name', 'session_type'] 