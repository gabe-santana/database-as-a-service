# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
from django_services import admin
from ..service.credential import CredentialService


class CredentialAdmin(admin.DjangoServicesAdmin):
    service_class = CredentialService
    search_fields = ("user", "database__name", "database__instance__name")
    list_filter = ("database", "database__instance", "database__instance__node__environment")
    list_display = ("user", "database", "instance_name")
    save_on_top = True

    def instance_name(self, credential):
        return credential.database.instance.name
    instance_name.admin_order_field = "database__instance__name"
