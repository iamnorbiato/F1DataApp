# G:\Learning\F1Data\F1Data_App\core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Endpoint para os filtros de Meeting (anos e meetings por ano)
    path('filters/meetings/', views.MeetingFilterAPIView.as_view(), name='meeting-filter-api'),
    path('sessions-by-meeting/', views.SessionListByMeeting.as_view(), name='sessions-by-meeting'),
]