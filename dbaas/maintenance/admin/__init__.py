# -*- coding:utf-8 -*-
from django.contrib import admin
from .. import models
from .maintenance import MaintenanceAdmin
from .host_maintenance import HostMaintenanceAdmin
from .database_upgrade import DatabaseUpgradeAdmin
from .database_resize import DatabaseResizeAdmin
from .database_change_parameter import DatabaseChangeParameterAdmin
from .database_create import DatabaseCreateAdmin
from .database_destroy import DatabaseDestroyAdmin
from .database_restore import DatabaseRestoreAdmin
from .database_reinstall_vm import DatabaseReinstallVMAdmin
from .database_configure_ssl import DatabaseConfigureSSLAdmin
from .host_migrate import HostMigrateAdmin
from .database_migrate import DatabaseMigrateAdmin
from .database_upgrade_patch import DatabaseUpgradePatchAdmin
from .recreate_slave import RecreateSlaveAdmin
from .update_ssl import UpdateSslAdmin
from .migrate_engine import DatabaseMigrateEngineAdmin
from .database_clone import DatabaseCloneAdmin
from .add_instances_to_database import AddInstancesToDatabaseAdmin
from .task_schedule import TaskScheduleAdmin
from .restart_database import RestartDatabaseAdmin
from .remove_instance_database import RemoveInstanceDatabaseAdmin
from .database_change_persistence import DatabaseChangePersistenceAdmin
from .database_set_ssl_required import DatabaseSetSSLRequiredAdmin
from .database_set_ssl_not_required import DatabaseSetSSLNotRequiredAdmin
from . database_upgrade_disk_type import DatabaseUpgradeDiskTypeAdmin
from . database_start_database_vm import DatabaseStartDatabaseVMAdmin
from . database_stop_database_vm import DatabaseStopDatabaseVMAdmin
from . database_auto_upgrade_vm_offering import DatabaseAutoUpgradeVMOferringAdmin
from . database_configure_db_params import DatabaseConfigureDBParamsAdmin


admin.site.register(models.Maintenance, MaintenanceAdmin)
admin.site.register(models.HostMaintenance, HostMaintenanceAdmin)
admin.site.register(models.DatabaseUpgrade, DatabaseUpgradeAdmin)
admin.site.register(models.DatabaseResize, DatabaseResizeAdmin)
admin.site.register(
    models.DatabaseChangeParameter, DatabaseChangeParameterAdmin
)
admin.site.register(models.DatabaseCreate, DatabaseCreateAdmin)
admin.site.register(models.DatabaseDestroy, DatabaseDestroyAdmin)
admin.site.register(models.DatabaseRestore, DatabaseRestoreAdmin)
admin.site.register(models.DatabaseReinstallVM, DatabaseReinstallVMAdmin)
admin.site.register(models.DatabaseConfigureSSL, DatabaseConfigureSSLAdmin)
admin.site.register(models.HostMigrate, HostMigrateAdmin)
admin.site.register(models.DatabaseMigrate, DatabaseMigrateAdmin)
admin.site.register(models.DatabaseUpgradePatch, DatabaseUpgradePatchAdmin)
admin.site.register(models.DatabaseMigrateEngine, DatabaseMigrateEngineAdmin)
admin.site.register(models.RecreateSlave, RecreateSlaveAdmin)
admin.site.register(models.UpdateSsl, UpdateSslAdmin)
admin.site.register(models.DatabaseClone, DatabaseCloneAdmin)
admin.site.register(models.AddInstancesToDatabase, AddInstancesToDatabaseAdmin)
admin.site.register(models.RemoveInstanceDatabase, RemoveInstanceDatabaseAdmin)
admin.site.register(models.TaskSchedule, TaskScheduleAdmin)
admin.site.register(models.RestartDatabase, RestartDatabaseAdmin)
admin.site.register(
    models.DatabaseChangePersistence, DatabaseChangePersistenceAdmin
)
admin.site.register(
    models.DatabaseSetSSLRequired, DatabaseSetSSLRequiredAdmin
)
admin.site.register(
    models.DatabaseSetSSLNotRequired, DatabaseSetSSLNotRequiredAdmin
)

admin.site.register(
    models.DatabaseUpgradeDiskType, DatabaseUpgradeDiskTypeAdmin
)

admin.site.register(
    models.DatabaseStartDatabaseVM, DatabaseStartDatabaseVMAdmin
)

admin.site.register(
    models.DatabaseStopDatabaseVM, DatabaseStopDatabaseVMAdmin
)

admin.site.register(
    models.DatabaseAutoUpgradeVMOffering, DatabaseAutoUpgradeVMOferringAdmin
)

admin.site.register(
    models.DatabaseConfigureDBParams, DatabaseConfigureDBParamsAdmin
)
