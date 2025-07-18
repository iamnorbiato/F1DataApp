# G:\Learning\F1Data\F1Data_App\core\views.py
from rest_framework import generics
from rest_framework.views import APIView 
from rest_framework.response import Response
from rest_framework import status

import logging
logger = logging.getLogger(__name__)

from datetime import datetime, timedelta, timezone  # timezone do datetime
from django.utils import timezone as django_timezone  # django.utils.timezone
from rest_framework.exceptions import ValidationError
 
from .models import Meetings, Sessions, Drivers, Weather, SessionResult, Laps, Pit, Stint, Position, Intervals, RaceControl, TeamRadio, CarData, Location, Circuit

from .serializers import (
    YearSerializer,
    MeetingFilterSerializer, 
    SessionSerializer,
    DriversSerializer,
    WeatherSerializer,
    SessionResultSerializer,
    LapsSerializer, PitSerializer, StintSerializer, PositionSerializer,
    IntervalsSerializer, RaceControlSerializer, TeamRadioSerializer, CarDataSerializer, LocationSerializer,
    MeetingSerializer, CircuitSerializer
)

# API para obter anos e meetings filtrados
class MeetingFilterAPIView(APIView):
    def get(self, request, *args, **kwargs):
        selected_year = request.query_params.get('year', None)

        if selected_year:
            try:
                selected_year = int(selected_year)
                meetings_queryset = Meetings.objects.filter(year=selected_year).values(
                    'meeting_key', 'year', 'country_name', 'meeting_name', 'circuit_short_name', 'circuit_key'
                ).distinct().order_by('meeting_key')

                serializer = MeetingFilterSerializer(meetings_queryset, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            except ValueError:
                return Response({"error": "O parâmetro 'year' deve ser um número inteiro."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            distinct_years = Meetings.objects.values_list('year', flat=True).distinct().order_by('year')
            return Response({'available_years': list(distinct_years)}, status=status.HTTP_200_OK)
        
# Endpoint para listar meetings filtrados por ano
class MeetingListByYear(generics.ListAPIView):
    serializer_class = MeetingSerializer

    def get_queryset(self):
        year = self.request.query_params.get('year', None)
        if year is not None:
            try:
                year = int(year)
                return Meetings.objects.filter(year=year).order_by('meeting_key')
            except ValueError:
                raise generics.ValidationError({"error": "O parâmetro 'year' deve ser um número inteiro."})
        return Meetings.objects.none()

# Endpoint para listar sessões filtradas por meeting_key
class SessionListByMeeting(generics.ListAPIView):
    serializer_class = SessionSerializer 

    def get_queryset(self):
        meeting_key = self.request.query_params.get('meeting_key', None)
        if meeting_key is not None:
            try:
                meeting_key = int(meeting_key)
                return Sessions.objects.filter(meeting_key=meeting_key).order_by('session_key')
            except ValueError:
                raise generics.ValidationError({"error": "O parâmetro 'meeting_key' deve ser um número inteiro."})
        return Sessions.objects.none()

# Endpoint para listar drivers filtradas por session_key
class DriversListBySession(generics.ListAPIView):
    serializer_class = DriversSerializer 

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key', None)
        if session_key is not None:
            try:
                session_key = int(session_key)
                return Drivers.objects.filter(session_key=session_key).order_by('driver_number')
            except ValueError:
                raise generics.ValidationError({"error": "O parâmetro 'session_key' deve ser um número inteiro."})
        return Drivers.objects.none()
    
#Endpoint para listar condições climáticas (Weather) filtradas por session_key
class WeatherListBySession(generics.ListAPIView):
    serializer_class = WeatherSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key', None)
        if session_key is not None:
            try:
                session_key = int(session_key)
                return Weather.objects.filter(session_key=session_key).order_by('session_date')
            except ValueError:
                raise generics.ValidationError({"error": "O parâmetro 'session_key' deve ser um número inteiro."})
        return Weather.objects.none()
    
# Endpoint para listar resultados de sessões filtradas por session_key
class SessionResultListBySession(generics.ListAPIView):
    serializer_class = SessionResultSerializer
    def get_queryset(self):
        session_key = self.request.query_params.get('session_key', None)
        if session_key is not None:
            try:
                session_key = int(session_key)
                return SessionResult.objects.filter(session_key=session_key).order_by('position')
            except ValueError:
                raise generics.ValidationError({"error": "O parâmetro 'session_key' deve ser um número inteiro."})
        return SessionResult.objects.none()
    
# Endpoint para listar voltas (Laps) filtradas por session_key
class LapsListBySessionAndDriver(generics.ListAPIView):
    serializer_class = LapsSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return Laps.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('lap_number')

# Endpoint para listar paradas (Pit Stops) filtradas por session_key e driver_number
class PitListBySessionAndDriver(generics.ListAPIView):
    serializer_class = PitSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return Pit.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('pit_stop_time')

#Endpoint para listar stints filtradas por session_key e driver_number
class StintListBySessionAndDriver(generics.ListAPIView):
    serializer_class = StintSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return Stint.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('stint_number')

#Endpoint para listar posições (Position) filtradas por session_key e driver_number
class PositionListBySessionAndDriver(generics.ListAPIView):
    serializer_class = PositionSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return Position.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('position')
    
#Endpoint para listar intervalos (Intervals) filtrados por session_key e driver_number
class IntervalsListBySessionAndDriver(generics.ListAPIView):
    serializer_class = IntervalsSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return Intervals.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('lap_number')

#Endpoint para listar informações de controle de corrida (RaceControl) filtradas por session_key
class RaceControlListBySession(generics.ListAPIView):
    serializer_class = RaceControlSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        if not session_key:
            raise generics.ValidationError({"error": "O parâmetro 'session_key' é obrigatório."})
        try:
            session_key = int(session_key)
        except ValueError:
            raise generics.ValidationError({"error": "O parâmetro 'session_key' deve ser um número inteiro."})
        
        return RaceControl.objects.filter(session_key=session_key).order_by('session_date')
    
# Endpoint para listar Team Radio filtradas por session_key e driver_number
class TeamRadioListBySessionAndDriver(generics.ListAPIView):
    serializer_class = TeamRadioSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return TeamRadio.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('date')

# Endpoint para listar dados do carro (CarData) filtrados por session_key e driver_number
class CarDataListBySessionAndDriver(generics.ListAPIView):
    serializer_class = CarDataSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        if not session_key or not driver_number:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' são obrigatórios."})
        try:
            session_key = int(session_key)
            driver_number = int(driver_number)
        except ValueError:
            raise generics.ValidationError({"error": "Os parâmetros 'session_key' e 'driver_number' devem ser números inteiros."})
        
        return CarData.objects.filter( session_key=session_key, driver_number=driver_number ).order_by('date')
    
class LocationListBySessionAndDriver(generics.ListAPIView):
    serializer_class = LocationSerializer

    def get_queryset(self):
        session_key = self.request.query_params.get('session_key')
        driver_number = self.request.query_params.get('driver_number')
        date_str = self.request.query_params.get('date')

        if not session_key or not driver_number or not date_str:
            raise ValidationError({"error": "Os parâmetros 'session_key', 'driver_number' e 'date' são obrigatórios."})

        try:
            session_key = int(session_key)
            driver_number = int(driver_number)

            if date_str.endswith('Z'):
                date_str = date_str[:-1]

            date = datetime.fromisoformat(date_str)

            if date.tzinfo is None:
                date = django_timezone.make_aware(date, timezone.utc)

        except ValueError:
            raise ValidationError({"error": "Parâmetros inválidos. 'session_key' e 'driver_number' devem ser inteiros, e 'date' deve estar no formato ISO 8601."})

        date_limit = date + timedelta(minutes=10)

        return Location.objects.filter(
            session_key=session_key,
            driver_number=driver_number,
            date__gte=date,
            date__lt=date_limit
        ).order_by('date')
        
#Endpoint para listar circuitos filtrados por circuit_key
class CircuitDetailByCircuitID(generics.RetrieveAPIView):
    serializer_class = CircuitSerializer

    def get_object(self):
        circuit_key = self.request.query_params.get('circuit_key', None)
        if circuit_key is None:
            raise ValidationError({"error": "O parâmetro 'circuit_key' é obrigatório."})
        try:
            circuit_key = int(circuit_key)
        except ValueError:
            raise ValidationError({"error": "O parâmetro 'circuit_key' deve ser um número inteiro."})
        
        try:
            return Circuit.objects.get(circuitid=circuit_key)
        except Circuit.DoesNotExist:
            raise ValidationError({"error": f"Circuito com circuitid={circuit_key} não encontrado."})
