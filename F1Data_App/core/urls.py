# G:\Learning\F1Data\F1Data_App\core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Endpoint para os filtros de Meeting (anos e meetings por ano)
    path('filters/meetings/', views.MeetingFilterAPIView.as_view(), name='meeting-filter'),
    path('sessions-by-meeting/', views.SessionListByMeeting.as_view(), name='sessions-by-meeting'),
    path('drivers-by-session/', views.DriversListBySession.as_view(), name='drivers-by-session'),
    path('weather-by-session/', views.WeatherListBySession.as_view(), name='weather-by-session'),
    path('session-results-by-session/', views.SessionResultListBySession.as_view(), name='session-results-by-session'),
    path('laps-by-session-and-driver/', views.LapsListBySessionAndDriver.as_view(), name='laps-by-session-and-driver'),
    path('pit-by-session-and-driver/', views.PitListBySessionAndDriver.as_view(), name='pit-by-session-and-driver'),
    path('stints-by-session-and-driver/', views.StintListBySessionAndDriver.as_view(), name='stints-by-session-and-driver'),
    path('position-by-session-and-driver/', views.PositionListBySessionAndDriver.as_view(), name='position-by-session-and-driver'),
    path('intervals-by-session-and-driver/', views.IntervalsListBySessionAndDriver.as_view(), name='intervals-by-session-and-driver'),
    path('race-control-by-session/', views.RaceControlListBySession.as_view(), name='race-control-by-session'),
    path('team-radio-by-session-and-driver/', views.TeamRadioListBySessionAndDriver.as_view(), name='team-radio-by-session-and-driver'),
    path('car-data-by-session-and-driver/', views.CarDataListBySessionAndDriver.as_view(), name='car-data-by-session-and-driver'),
    path('location-by-session-and-driver/', views.LocationListBySessionAndDriver.as_view(), name='location-by-session-and-driver'),
    path('circuit/', views.CircuitDetailByCircuitID.as_view(), name='circuit-detail'),
    path('min-max-location-date/', views.MinMaxLocationDate.as_view(), name='min-max-location-date'),
]