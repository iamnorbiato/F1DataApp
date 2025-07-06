# f1data_project/urls.py
from django.contrib import admin
from django.urls import path, include # <--- ADICIONADO 'include' de volta

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api-auth/', include('rest_framework.urls')), # <--- ADICIONADO de volta: URLs de autenticação do DRF
    path('api/', include('core.urls')), # <--- ADICIONADO de volta: URLs da sua API do app 'core'
]