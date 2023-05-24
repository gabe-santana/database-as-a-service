from backup.tasks import mysql_binlog_save
from workflow.steps.mysql.util import get_replication_information_from_file, \
    change_master_to, start_slave, build_uncomment_skip_slave_script,\
    build_comment_skip_slave_script, get_replication_info
from volume_provider import AddAccessRestoredVolume, MountDataVolumeRestored, \
    RestoreSnapshot, UnmountActiveVolume, DetachActiveVolume, \
    AttachDataVolumeRestored
from zabbix import ZabbixStep
from base import BaseInstanceStep
from workflow.steps.util import test_bash_script_error
from time import sleep


CHECK_REPLICATION_ATTEMPS = 12
CHECK_REPLICATION_WAIT = 10


class MySQLStep(BaseInstanceStep):

    def undo(self):
        pass

    def run_script(self, script):
        raise Exception(
            "U must use the new method. run_script of HostSSH class"
        )
        output = {}
        return_code = exec_remote_command_host(
            self.host, script, output
        )
        if return_code != 0:
            raise EnvironmentError(
                'Could not execute script {}: {}'.format(
                    return_code, output
                )
            )

    def get_instance_server_id(self, instance):
        query = "show variables like 'server_id'"
        instance_result = self.driver.query(query, instance)
        instance_server_id = int(instance_result[0]['Value'])
        return instance_server_id


class SetMasterRestore(MySQLStep):

    def __unicode__(self):
        return "Set master position..."

    def do(self):
        pair = self.restore.instances_pairs()[0]
        log_file, log_pos = get_replication_information_from_file(
            pair.master.hostname
        )

        secondary = pair.master
        if self.instance == secondary:
            secondary = pair.slave

        change_master_to(
            self.instance, secondary.hostname.address, log_file, log_pos
        )


class SetMasterRecreateSlave(MySQLStep):

    def __unicode__(self):
        return "Set master position on recreate slave..."

    def do(self):
        log_file, log_pos = get_replication_information_from_file(
            self.instance.hostname
        )
        master_instance = self.driver.get_master_instance()
        change_master_to(
            self.instance,
            master_instance.hostname.address,
            log_file, log_pos
        )
        change_master_to(
            master_instance,
            self.instance.hostname.address,
            log_file, log_pos
        )


class SetReadOnlyMigrate(MySQLStep):
    def __unicode__(self):
        return "Change master mode to read only..."

    @property
    def is_valid(self):
        return self.instance == self.infra.instances.last()

    def change_variable(self, instance, field, value):
        if not self.is_valid:
            return
        self.driver.set_configuration(instance, field, value)

    def do(self):
        self.change_variable(self.instance, 'read_only', 'ON')

    def undo(self):
        self.change_variable(self.instance, 'read_only', 'OFF')


class SetSourceInstancesReadOnlyMigrate(SetReadOnlyMigrate):
    @property
    def is_valid(self):
        return True

    def undo(self):
        if self.instance == self.infra.instances.first():
            self.change_variable(self.instance, 'read_only', 'OFF')


class SetFirstTargetInstanceReadWriteMigrate(SetSourceInstancesReadOnlyMigrate):

    def do(self):
        if self.instance == self.infra.instances.first():
            self.change_variable(
                self.instance.future_instance, 'read_only', 'OFF'
            )
    def undo(self):
        self.change_variable(
            self.instance.future_instance, 'read_only', 'ON'
        )

class SetReadWriteMigrate(SetReadOnlyMigrate):
    def __unicode__(self):
        return "Change new instance to read write..."

    def do(self):
        self.instance.address = self.host.address
        return super(SetReadWriteMigrate, self).undo()

    def undo(self):
        return super(SetReadWriteMigrate, self).do()


class StartSlave(MySQLStep):

    def __unicode__(self):
        return "Start slave..."

    def do(self):
        start_slave(self.instance)


class ConfigureFoxHARestore(MySQLStep):

    def __unicode__(self):
        return "Configuring FoxHA..."

    def do(self):
        driver = self.infra.get_driver()
        if self.restore.is_master(self.instance):
            driver.set_master(self.instance)
        else:
            driver.set_read_ip(self.instance)


class DisableReplication(MySQLStep):
    def __unicode__(self):
        return "Disable replication..."

    @property
    def script(self):
        return build_uncomment_skip_slave_script()

    @property
    def is_valid(self):
        return self.instance == self.infra.instances.last()

    def do(self):
        if not self.is_valid:
            return
        script = build_uncomment_skip_slave_script()
        # self.run_script(script)
        self.host.ssh.run_script(script)

    def undo(self):
        if not self.is_valid:
            return
        script = build_comment_skip_slave_script()
        # self.run_script(script)
        self.host.ssh.run_script(script)


class DisableReplicationRecreateSlave(DisableReplication):

    @property
    def is_valid(self):
        return True


class EnableReplication(DisableReplication):
    def __unicode__(self):
        return "Enable replication..."

    def do(self):
        return super(EnableReplication, self).undo()

    def undo(self):
        return super(EnableReplication, self).do()


class EnableReplicationRecreateSlave(EnableReplication):

    @property
    def is_valid(self):
        return True


class SaveMySQLBinlog(MySQLStep):

    def __unicode__(self):
        return "Saving binlog position..."

    @property
    def is_valid(self):
        return self.restore.is_master(self.instance)

    def do(self):
        if not self.is_valid:
            return

        driver = self.infra.get_driver()
        client = driver.get_client(self.instance)


class SaveMysqlBinlogToStopDatabase(MySQLStep):

    def __unicode__(self):
        return "Saving binlog position to stop database..."

    def do(self):
        driver = self.infra.get_driver()
        client = driver.get_client(self.instance)

        if 'MySQL' in type(driver).__name__:
            mysql_binlog_save(client, self.instance)


class RestoreSnapshotMySQL(RestoreSnapshot):

    @property
    def snapshot(self):
        return self.restore.group.backups.first()

    @property
    def disk_host(self):
        return self.host


class DiskRestoreMySQL(MySQLStep):

    @property
    def is_valid(self):
        return True


class AddDiskPermissionsRestoredDiskMySQL(
    DiskRestoreMySQL, AddAccessRestoredVolume
):
    pass


class UnmountOldestExportRestoreMySQL(
    DiskRestoreMySQL, UnmountActiveVolume
):
    pass


class DetachOldestExportRestoreMySQL(
    DiskRestoreMySQL, DetachActiveVolume
):
    pass


class MountNewerExportRestoreMySQL(DiskRestoreMySQL, MountDataVolumeRestored):
    pass


class AttachNewerExportRestoreMySQL(DiskRestoreMySQL, AttachDataVolumeRestored):
    pass


class ZabbixVip(ZabbixStep):

    @property
    def is_valid(self):
        return self.instance == self.infra.instances.first()

    @property
    def vip_instance_dns(self):
        return self.zabbix_provider.mysql_infra_dns_from_endpoint_dns


class CreateAlarmsVip(ZabbixVip):

    def __unicode__(self):
        return "Creating monitoring to FoxHA Vip..."

    @property
    def is_valid(self):
        return self.instance == self.infra.instances.first()

    def do(self):
        if not self.is_valid:
            return

        self.zabbix_provider.create_mysqlvip_monitor(self.vip_instance_dns)

    def undo(self):
        DestroyAlarmsVip(self.instance).do()


class CreateAlarmsVipForUpgrade(CreateAlarmsVip):
    @property
    def target_plan(self):
        return self.plan.engine_equivalent_plan


class CreateAlarmsVipForMigradeEngine(CreateAlarmsVip):
    @property
    def target_plan(self):
        return self.plan.migrate_engine_equivalent_plan


class DestroyAlarmsVip(ZabbixVip):

    def __unicode__(self):
        return "Destroying monitoring to FoxHA Vip..."

    def do(self):
        if not self.is_valid:
            return

        self.zabbix_provider.delete_instance_monitors(self.vip_instance_dns)

    def undo(self):
        CreateAlarmsVip(self.instance).do()


class CreateAlarmsVipDatabaseMigrate(CreateAlarmsVip):
    @property
    def environment(self):
        return self.infra.environment


class DestroyAlarmsVipDatabaseMigrate(DestroyAlarmsVip):
    @property
    def environment(self):
        return self.infra.environment

    def undo(self):
        CreateAlarmsVipDatabaseMigrate(self.instance).do()


class SetFilePermission(MySQLStep):
    def __unicode__(self):
        return "Setting file permition..."

    @property
    def script(self):
        return test_bash_script_error() + """
            chown mysql:mysql /data
            die_if_error "Error executing chown mysql:mysql /data"
            chown -R mysql:mysql /data/*
            die_if_error "Error executing chown -R mysql:mysql /data/*"
            """

    def do(self):
        self.host.ssh.run_script(self.script)


class RunMySQLUpgrade(MySQLStep):
    def __unicode__(self):
        return "Executing mysql_upgrade..."

    @property
    def script(self):
        return "mysql_upgrade -u{} -p{}".format(
            self.infra.user, self.infra.password)

    def do(self):
        # self.run_script(self.script)
        self.host.ssh.run_script(self.script)


class AuditPlugin(MySQLStep):
    @property
    def audit_plugin_status(self):
        query = """SELECT plugin_name, plugin_status
        FROM INFORMATION_SCHEMA.PLUGINS
        WHERE plugin_name = 'audit_log';"""

        ret_query = self.driver.query(
            query_string=query, instance=self.instance)
        if len(ret_query) == 0:
            return False
        if ret_query[0]['plugin_status'] != 'ACTIVE':
            return False
        return True


class InstallAuditPlugin(AuditPlugin):
    def __unicode__(self):
        return "Installing audit plugin..."

    @property
    def query(self):
        return "INSTALL PLUGIN audit_log SONAME 'audit_log.so';"

    def do(self):
        if not self.audit_plugin_status:
            self.driver.query(query_string=self.query, instance=self.instance)


class CheckIfAuditPluginIsInstalled(AuditPlugin):
    def __unicode__(self):
        return "Checking if audit plugin is installed..."

    def do(self):
        if not self.audit_plugin_status:
            raise EnvironmentError('The audit plugin is not installed.')


class SkipSlaveStart(MySQLStep):
    def __unicode__(self):
        return "Skipping slave start parameter..."

    @property
    def script(self):
        return "echo 'skip_slave_start = 1' >> /etc/my.cnf"

    def do(self):
        # self.run_script(self.script)
        self.host.ssh.run_script(self.script)


class DisableLogBin(MySQLStep):
    def __unicode__(self):
        return "Disable binary loggin..."

    @property
    def script(self):
        return "sed -e 's/^log_bin/#log_bin/' -i /etc/my.cnf"

    def do(self):
        # self.run_script(self.script)
        self.host.ssh.run_script(self.script)


class SetServerid(MySQLStep):
    def __unicode__(self):
        return "Set serverid to {}...".format(self.serverid)

    @property
    def serverid(self):
        return int(self.instance.dns.split('-')[1])

    @property
    def script(self):
        return """
        echo ""; echo $(date "+%Y-%m-%d %T") "- Creating the server id db file"
        \n(cat <<EOF_DBAAS
        \n[mysqld]
        \nserver_id={}
        \nEOF_DBAAS
        \n) >  /etc/server_id.cnf
        """.format(self.serverid)

    def do(self):
        # self.run_script(self.script)
        self.host.ssh.run_script(self.script)


class SetServeridMigrate(SetServerid):
    @property
    def serverid(self):
        server_id = super(SetServeridMigrate, self).serverid
        source_server_id = self.get_instance_server_id(self.instance)
        if source_server_id == server_id:
            server_id += 2
        return server_id


class SetReplicationHostMigrate(MySQLStep):

    def __unicode__(self):
        return "Set replication on host migrate..."

    @property
    def master_instance(self):
        return self.driver.get_master_instance()

    @property
    def is_valid(self):
        return True

    def do(self):
        if not self.is_valid:
            return
        log_file, log_pos = get_replication_information_from_file(self.host)
        change_master_to(self.master_instance,
                         self.host.address, log_file, log_pos)

    def undo(self):
        raise Exception("There is no rollback for this step.")


class SetReplicationRecreateSlave(SetReplicationHostMigrate):

    def __unicode__(self):
        return "Set replication on slave instance..."

    def do(self):
        if not self.is_valid:
            return
        master_instance = self.master_instance
        master_host = master_instance.hostname
        log_file, log_pos = get_replication_information_from_file(master_host)
        change_master_to(self.instance, master_host.address, log_file, log_pos)


class SetReplicationLastInstanceMigrate(SetReplicationHostMigrate):
    @property
    def is_valid(self):
        return self.instance == self.infra.instances.last()

    @property
    def host(self):
        # database_migrate = self.host_migrate.database_migrate
        # return database_migrate.hosts.exclude(id=self.\
        #   host_migrate.id).first().host.future_host
        return self.infra.instances.exclude(id=self.instance.id).\
                first().hostname.future_host

    @property
    def master_instance(self):
        self.instance.address = self.host_migrate.host.future_host.address
        return self.instance


class SetReplicationFirstInstanceMigrate(SetReplicationLastInstanceMigrate):
    @property
    def is_valid(self):
        return self.instance == self.infra.instances.first()

    def do(self):
        if not self.is_valid:
            return
        instance = self.instance
        instance.address = self.host.address
        client = self.driver.get_client(instance)
        client.query('show master status')
        r = client.store_result()
        row = r.fetch_row(maxrows=0, how=1)
        log_file = row[0]['File']
        log_pos = row[0]['Position']
        change_master_to(self.master_instance, self.host.address,
                         log_file, log_pos)


class CheckReplicationDBMigrate(MySQLStep):
    def __unicode__(self):
        return "Checking replication..."

    def check_replication(self):
        for _ in range(CHECK_REPLICATION_ATTEMPS):
            if (self.driver.is_replication_ok(self.instance) and
                self.driver.is_replication_ok(self.instance.future_instance)):
                return True
            sleep(CHECK_REPLICATION_WAIT)

    def do(self):
        self.check_replication()

    def undo(self):
        self.check_replication()


class ReconfigureReplicationDBMigrate(MySQLStep):
    def __unicode__(self):
        return "Reconfiguring replication..."

    @property
    def source_instances(Self):
        return self.infra.instances.filter(future_instance__isnull=False)

    @property
    def target_instances(Self):
        return self.infra.instances.filter(future_instance__isnull=True)

    def get_new_master_same_env(self, instance):
        if instance.future_instance:
            return self.infra.instances.filter(
                future_instance__isnull=False
            ).exclude(id=instance.id)[0]
        else:
            return self.infra.instances.filter(
                future_instance__isnull=True
            ).exclude(id=instance.id)[0]

    def get_new_master_new_env(self, instance):
        if instance.future_instance:
            return instance.future_instance
        else:
            return self.infra.instances.get(future_instance=instance)

    def _change_master_to(self, instance, master):
        log_file, log_position = get_replication_info(master)
        change_master_to(instance, master.address, log_file, log_position)

    def do(self):
        master = self.get_new_master_new_env(self.instance)
        self._change_master_to(self.instance, master)

        master = self.get_new_master_same_env(self.instance.future_instance)
        self._change_master_to(self.instance.future_instance, master)

    def undo(self):
        master = self.get_new_master_same_env(self.instance)
        self._change_master_to(self.instance, master)

        master = self.get_new_master_new_env(self.instance.future_instance)
        self._change_master_to(self.instance.future_instance, master)
