from django.http import HttpResponse
from django.urls import path

from weisswurstrunde import views

urlpatterns = [
    path("health/", lambda request: HttpResponse("ok")),
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("register/", views.register, name="register"),
    path("logout/", views.logout_view, name="logout"),
    path("orders/", views.orders, name="orders"),
    path("order-history/", views.order_history, name="order_history"),
    path("orders/<int:order_id>/", views.edit_order, name="edit_order"),
    path("events/<int:event_id>/cancel/", views.cancel_event, name="cancel_event"),
    path("events/<int:event_id>/orders/add/", views.add_order, name="add_order"),
    path("participants/", views.participants, name="participants"),
    path("profile/", views.profile, name="profile"),
    path("products/", views.products, name="products"),
    path("payments/", views.payments, name="payments"),
    path("history/", views.history, name="history"),
    path("history/<int:user_id>/", views.history, name="user_history"),
    path("correction/<int:entry_id>/", views.correction, name="correction"),
    path("paypal/<uuid:payment_id>/sync/", views.paypal_sync, name="paypal_sync"),
    path("paypal/<uuid:payment_id>/delete/", views.paypal_delete, name="paypal_delete"),
]
