# G:\Learning\F1Data\F1Data_App\core\views.py
from rest_framework import generics
from rest_framework.views import APIView 
from rest_framework.response import Response
from rest_framework import status

from .models import Meetings, Sessions
from .serializers import (
    YearSerializer,
    MeetingFilterSerializer, 
    SessionSerializer 
)

# API para obter anos e meetings filtrados
class MeetingFilterAPIView(APIView):
    def get(self, request, *args, **kwargs):
        selected_year = request.query_params.get('year', None)

        if selected_year:
            try:
                selected_year = int(selected_year)
                meetings_queryset = Meetings.objects.filter(year=selected_year).values(
                    'meeting_key', 'year', 'country_name', 'meeting_name', 'circuit_short_name'
                ).distinct().order_by('meeting_key')

                serializer = MeetingFilterSerializer(meetings_queryset, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            except ValueError:
                return Response({"error": "O parâmetro 'year' deve ser um número inteiro."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            distinct_years = Meetings.objects.values_list('year', flat=True).distinct().order_by('year')
            return Response({'available_years': list(distinct_years)}, status=status.HTTP_200_OK)

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
