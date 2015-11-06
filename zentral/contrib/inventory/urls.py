from django.conf.urls import url
from django.views.decorators.csrf import csrf_exempt

from . import views

urlpatterns = [
    url(r'^$', views.IndexView.as_view(), name='index'),
    url(r'^machine/(?P<serial_number>\S+)/events/$', views.MachineEventsView.as_view(), name='machine_events'),
    url(r'^machine/(?P<serial_number>\S+)/$', views.MachineView.as_view(), name='machine'),
    url(r'^api/machine/$', csrf_exempt(views.MachineAPIView.as_view()), name='machine_api'),
]
