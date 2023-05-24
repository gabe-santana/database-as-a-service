# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
from django.conf.urls import patterns, url
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.html import format_html
from .database_maintenance_task import DatabaseMaintenanceTaskAdmin
from ..models import DatabaseConfigureDBParams
from notification.tasks import TaskRegister


class DatabaseConfigureDBParamsAdmin(DatabaseMaintenanceTaskAdmin):
    search_fields = (
        "database__name", "database__databaseinfra__name", "task__id", "task__task_id"
    )

    list_display = (
        "database", "database_team", "current_step", "current_step_class", "friendly_status",
        "maintenance_action", "link_task", "started_at", "finished_at"
    )

    readonly_fields = (
        "database", "task", "started_at", 
        "link_task", "finished_at", "status",
        "maintenance_action", "task_schedule",
        
    )

    def maintenance_action(self, maintenance):
        if not maintenance.is_status_error:
            return 'N/A'

        if not maintenance.can_do_retry:
            return 'N/A'

        url = "/admin/maintenance/databaseconfiguredbparams/{}/retry/".format(
            maintenance.id
        )
        html = ("<a title='Retry' class='btn btn-info' "
                "href='{}'>Retry</a>").format(url)
        return format_html(html)

    def get_urls(self):
        base = super(DatabaseConfigureDBParamsAdmin, self).get_urls()

        admin = patterns(
            '',
            url(
                r'^/?(?P<configure_db_params_id>\d+)/retry/$',
                self.admin_site.admin_view(self.retry_view),
                name="configure_db_params_retry"
            ),
        )
        return admin + base

    def retry_view(self, request, configure_db_params_id):
        retry_from = get_object_or_404(DatabaseConfigureDBParams, pk=configure_db_params_id)

        error = False
        if not retry_from.is_status_error:
            error = True
            messages.add_message(
                request, messages.ERROR,
                "You can not do retry because configure db params status is '{}'".format(
                    retry_from.get_status_display()
                ),
            )

        if not retry_from.can_do_retry:
            error = True
            messages.add_message(
                request, messages.ERROR, "Configure DB Params retry is disabled"
            )

        if error:
            return HttpResponseRedirect(
                reverse(
                    'admin:maintenance_configuredbparams_change',
                    args=(configure_db_params_id,)
                )
            )

        TaskRegister.configure_db_params(
            database=retry_from.database,
            user=request.user,
            retry_from=retry_from
        )

        url = reverse('admin:notification_taskhistory_changelist')
        filters = "user={}".format(request.user.username)
        return HttpResponseRedirect('{}?{}'.format(url, filters))
