# G:\Learning\F1Data\F1Data_App\core\views.py
from rest_framework import generics
from rest_framework.views import APIView 
from rest_framework.response import Response
from rest_framework import status

import logging
logger = logging.getLogger(__name__)

from datetime import datetime, timedelta, timezone
from django.utils import timezone as django_timezone

from django.db.models import F, Case, When, Value, IntegerField
from django.db.models.functions import Cast
import math
from django.contrib.postgres.fields import ArrayField

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

# --- CONSTANTES GLOBAIS PARA ORDENAÇÃO ---
# (Certifique-se de que estas estejam no TOPO do arquivo, abaixo das importações)
POS_MAX_FINISHER = 99 

# Bases para STATUS (valores ABSOLUTOS de rank baixo)
STATUS_NC_BASE = 100 # Not Classified (manter para fallback, mas o user disse que não vai ocorrer)
STATUS_DNF_BASE = 200 # Did Not Finish
STATUS_DNS_BASE = 300 # Did Not Start (agora inclui NULLs)
STATUS_DSQ_BASE = 400 # Disqualified (O MAIOR valor = O PIOR RANK)
POS_NULL_BASE = 500 # Para strings inesperadas que não são status conhecidos

# Bases para Qualificação
BASE_Q2 = 1000 
BASE_Q1 = 2000 
BASE_NO_TIME = 9000 # Base para DNQ / Sem tempo em Qualificação

MAX_LAPS_VAL = 1000 
# --- FIM DAS CONSTANTES GLOBAIS ---

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
class SessionResultListBySession(APIView):
    def get(self, request, *args, **kwargs):
        session_key = request.query_params.get('session_key')

        if not session_key:
            return Response({"error": "session_key is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session_key = int(session_key) 
        except ValueError:
            return Response({"error": "session_key deve ser um número inteiro."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session_obj = Sessions.objects.get(session_key=session_key)
            session_type = session_obj.session_type
        except Sessions.DoesNotExist:
            return Response({"error": f"Sessão com session_key {session_key} não encontrada."}, status=status.HTTP_404_NOT_FOUND)

        session_results_queryset = SessionResult.objects.filter(session_key=session_key)

        def _calculate_sort_position_value(item_data, current_session_type):
            pos_str = item_data['position'] 
            is_dnf = item_data['dnf']
            is_dns = item_data['dns']
            is_dsq = item_data['dsq']
            num_laps = item_data['number_of_laps'] if item_data['number_of_laps'] is not None else 0 

            try:
                position_numeric_value = int(pos_str)
                return position_numeric_value 
            except (ValueError, TypeError): # Se pos_str não é um número (ex: 'DQ', 'NC', None)
                
                if current_session_type == 'Race':
                    # Ordem: Normal -> DNF (por laps) -> DNS (inclui NULLs) -> DSQ (último)
                    if is_dnf or pos_str == 'DNF': # DNF (por laps)
                        return STATUS_DNF_BASE - num_laps
                    elif is_dns or pos_str == 'DNS' or pos_str is None: # DNS (agora inclui NULLs)
                        return STATUS_DNS_BASE - num_laps
                    elif is_dsq or pos_str == 'DQ': # DSQ (ÚLTIMO)
                        return STATUS_DSQ_BASE - num_laps
                    # else: Fallback para strings não capturadas (User disse que NC não haverá)
                    # Usamos POS_NULL_BASE para qualquer outra coisa inesperada
                    else: 
                        return POS_NULL_BASE - num_laps
                
                elif current_session_type == 'Practice': # Lógica para Practice (já existente)
                    # Assume que non-numéricos/null/status são agrupados após finishers e ordenados por laps
                    return (POS_MAX_FINISHER + 1) + (MAX_LAPS_VAL - num_laps)
                
                elif current_session_type == 'Qualifying': # Lógica para Qualifying (já existente)
                    q3_time = item_data['duration'][0] if item_data['duration'] and len(item_data['duration']) > 0 and item_data['duration'][0] is not None else None
                    q2_time = item_data['duration'][1] if item_data['duration'] and len(item_data['duration']) > 1 and item_data['duration'][1] is not None else None
                    q1_time = item_data['duration'][2] if item_data['duration'] and len(item_data['duration']) > 2 and item_data['duration'][2] is not None else None

                    try:
                        q3_time_float = float(q3_time) if q3_time is not None else math.inf
                    except (ValueError, TypeError): q3_time_float = math.inf
                    
                    try:
                        q2_time_float = float(q2_time) if q2_time is not None else math.inf
                    except (ValueError, TypeError): q2_time_float = math.inf
                    
                    try:
                        q1_time_float = float(q1_time) if q1_time is not None else math.inf
                    except (ValueError, TypeError): q1_time_float = math.inf

                    if q3_time_float != math.inf:
                        return q3_time_float
                    elif q2_time_float != math.inf:
                        return BASE_Q2 + q2_time_float 
                    elif q1_time_float != math.inf:
                        return BASE_Q1 + q1_time_float 
                    else: # Se não tem tempo em nenhuma etapa, vai para o final (DNQ)
                        return BASE_NO_TIME + (MAX_LAPS_VAL - num_laps)

                else: # Fallback para outros tipos de sessão não especificados (vão para o final)
                    return POS_NULL_BASE - num_laps
        
        driver_numbers_in_session = session_results_queryset.values_list('driver_number', flat=True).distinct()
        
        drivers_data = {
            d.driver_number: d
            for d in Drivers.objects.filter(driver_number__in=driver_numbers_in_session)
        }

        combined_results = []
        for sr in session_results_queryset:
            driver_obj = drivers_data.get(sr.driver_number)
            
            combined_item = {
                'session_type': session_type, 
                'position': sr.position, 
                'calculated_position': _calculate_sort_position_value(
                    { 
                        'position': sr.position, 'dnf': sr.dnf, 'dns': sr.dns, 'dsq': sr.dsq,
                        'number_of_laps': sr.number_of_laps, 'duration': sr.duration 
                    },
                    session_type 
                ), 
                'driver_number': sr.driver_number,
                'number_of_laps': sr.number_of_laps,
                'dnf': sr.dnf,
                'dns': sr.dns,
                'dsq': sr.dsq,
                'duration': sr.duration, 
                'gap_to_leader': sr.gap_to_leader, 
                
                'broadcast_name': driver_obj.broadcast_name if driver_obj and driver_obj.broadcast_name is not None else 'Desconhecido',
                'team_name': driver_obj.team_name if driver_obj and driver_obj.team_name is not None else 'Desconhecido',
                'headshot_url': driver_obj.headshot_url if driver_obj and driver_obj.headshot_url is not None else None,
                
                'meeting_key': sr.meeting_key,
                'session_key': sr.session_key,
                'pos_q1': None, # Inicializa pos_q1
                'pos_q2': None, # Inicializa pos_q2
            }
            combined_results.append(combined_item)

        # --- LÓGICA DE CÁLCULO DE POS Q1/Q2 (APENAS PARA QUALIFYING) ---
        if session_type == 'Qualifying':
            # 1. Calcular Pos Q1
            q1_participants_data = [] 
            for idx, item in enumerate(combined_results):
                q1_time_val = item['duration'][2] if item['duration'] and len(item['duration']) > 2 else None
                if q1_time_val is not None: 
                    try:
                        q1_time_float = float(q1_time_val)
                        q1_participants_data.append({'original_index': idx, 'time': q1_time_float})
                    except (ValueError, TypeError):
                        pass 

            q1_participants_data.sort(key=lambda x: x['time'])

            for rank, participant in enumerate(q1_participants_data, 1):
                original_item_index = participant['original_index']
                combined_results[original_item_index]['pos_q1'] = rank

            # 2. Calcular Pos Q2
            q2_participants_data = [] 
            for idx, item in enumerate(combined_results):
                q2_time_val = item['duration'][1] if item['duration'] and len(item['duration']) > 1 else None
                if q2_time_val is not None: 
                    try:
                        q2_time_float = float(q2_time_val)
                        q2_participants_data.append({'original_index': idx, 'time': q2_time_float})
                    except (ValueError, TypeError):
                        pass

            q2_participants_data.sort(key=lambda x: x['time'])

            for rank, participant in enumerate(q2_participants_data, 1):
                original_item_index = participant['original_index']
                combined_results[original_item_index]['pos_q2'] = rank
        # --- FIM DA LÓGICA DE CÁLCULO DE POS Q1/Q2 ---

        # 6. Ordenação Final Completa em Python (Mantida como estava)
        final_sorted_results = sorted(combined_results, key=lambda x: x['calculated_position'])

        # 7. Serializar e Retornar a Resposta
        serializer = SessionResultSerializer(final_sorted_results, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

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
