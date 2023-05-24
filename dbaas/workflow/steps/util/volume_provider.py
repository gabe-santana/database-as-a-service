# -*- coding: utf-8 -*-
import logging
from datetime import datetime
from logging import exception
from time import sleep

from requests import post, delete, get
from backup.models import Snapshot
from dbaas_credentials.models import CredentialType
from workflow.steps.util.base import HostProviderClient
from util import get_credentials_for
from physical.models import Volume
from base import BaseInstanceStep
from maintenance.models import DatabaseMigrate


LOG = logging.getLogger(__name__)

class VolumeProviderException(Exception):
    pass


class VolumeProviderRemoveSnapshotMigrate(VolumeProviderException):
    pass


class VolumeProviderRemoveVolumeMigrate(VolumeProviderException):
    pass


class VolumeProviderGetSnapshotState(VolumeProviderException):
    pass


class VolumeProviderScpFromSnapshotCommand(VolumeProviderException):
    pass

class VolumeProviderRsyncFromSnapshotCommand(VolumeProviderException):
    pass

class VolumeProviderAddHostAllowCommand(VolumeProviderException):
    pass


class VolumeProviderCreatePubKeyCommand(VolumeProviderException):
    pass


class VolumeProviderRemovePubKeyCommand(VolumeProviderException):
    pass


class VolumeProviderRemoveHostAllowCommand(VolumeProviderException):
    pass


class VolumeProviderSnapshotHasWarningStatusError(VolumeProviderException):
    pass


class VolumeProviderSnapshotNotFoundError(VolumeProviderException):
    pass


class VolumeProviderSnapshotHasErrorStatus(VolumeProviderException):
    pass


class InvalidEnvironmentException(VolumeProviderException):
    pass


class VolumeProviderBase(BaseInstanceStep):

    def __init__(self, instance, force_environment=None):
        super(VolumeProviderBase, self).__init__(instance)
        self.force_environment = force_environment
        self._credential = None
        self.host_prov_client = HostProviderClient(self.environment)
        self._host_vm = None
        self.base_snapshot = None

    @property
    def driver(self):
        return self.infra.get_driver()

    @property
    def environment(self):
        if self.force_environment is not None:
            return self.force_environment

        return super(VolumeProviderBase, self).environment

    @property
    def credential(self):
        if self.force_environment is not None:
            return self.credential_by_env(self.force_environment)

        if not self._credential:
            self._credential = get_credentials_for(
                self.environment, CredentialType.VOLUME_PROVIDER
            )
        return self._credential

    def credential_by_env(self, env=None):
        if not env:
            return self.credential

        return get_credentials_for(
            env, CredentialType.VOLUME_PROVIDER
        )

    @property
    def volume(self):
        return self.host.volumes.get(is_active=True)

    @property
    def inactive_volume(self):
        return self.host.volumes.filter(is_active=False).last() or None

    @property
    def volume_migrate(self):
        return self.host_migrate.host.volumes.get(is_active=True)

    @property
    def provider(self):
        return self.credential.project

    @property
    def base_uri(self):
        return "{}/{}/{}/".format(
            self.credential.endpoint,
            self.provider,
            self.environment
        )

    @property
    def migration_in_progress(self):
        return self.infra.migration_in_progress

    @property
    def headers(self):
        header = {}
        if self.pool:
            header = self.pool.as_headers
        header["K8S-Namespace"] = self.infra.name
        return header

    @property
    def host_vm(self):
        if self._host_vm is None:
            self._host_vm = self.host_prov_client.get_vm_by_host(self.host)

        return self._host_vm

    @property
    def master_host_vm(self):
        host = self.infra.get_driver().get_master_instance()
        if isinstance(host, list):
            host = host[0]

        host = host.hostname
        return self.host_prov_client.get_vm_by_host(
            host
        )

    def create_volume(self, group, size_kb, to_address='', snapshot_id=None,
                      is_active=True, zone=None, vm_name=None, disk_offering_type=None):
        url = self.base_uri + "volume/new"

        data = {
            "group": group,
            "size_kb": size_kb,
            "to_address": to_address,
            "snapshot_id": snapshot_id,
            "zone": zone,
            "vm_name": vm_name,
            "team_name": self.team_name,
            "engine": self.engine.name,
            "db_name": self.database_name,
            "disk_offering_type": disk_offering_type
        }

        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)

        volume = Volume()
        volume.host = self.host
        volume.identifier = response.json()['identifier']
        volume.total_size_kb = self.infra.disk_offering.size_kb
        volume.is_active = is_active
        volume.disk_offering_type = disk_offering_type
        volume.save()
        return volume

    def destroy_volume(self, volume):
        from backup.tasks import remove_snapshot_backup
        snapshots = volume.backups.filter(purge_at__isnull=True).order_by('-created_at')
        for i, snapshot in enumerate(snapshots):
            if i != 0:
                self.force_environment = snapshot.environment
                remove_snapshot_backup(snapshot, self)

        self.force_environment = None

        url = "{}volume/{}".format(self.base_uri, volume.identifier)
        response = delete(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        volume.delete()

    def move_disk(self, volume, zone):
        url = "{}move/{}".format(self.base_uri, volume.identifier)
        data = {
            'zone': zone
        }
        response = post(url, json=data, headers=self.headers)

        if not response.ok:
            raise IndexError(response.content, response)

        return response.json()

    def detach_disk(self, volume):
        url = "{}detach/{}/".format(self.base_uri, volume.identifier)
        response = post(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)

        return response.json()

    @property
    def should_migrate_with_new_disk(self):
        url = "{}new-disk-migration".format(
                self.base_uri)
        response = get(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return bool(response.json()["create_new_disk"])

    def attach_disk(self, volume):
        url = "{}attach/{}/".format(self.base_uri, volume.identifier)
        data = {
            'host_vm': self.host_vm.name,
            'host_zone': self.host_vm.zone
        }

        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def attach_disk_region(self, volume, zone, name):
        url = "{}attach/{}/".format(self.base_uri, volume.identifier)
        data = {
            'host_vm': name,
            'host_zone': zone
        }
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def get_volume(self, volume):
        url = "{}volume/{}".format(self.base_uri, volume.identifier)
        response = get(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def get_path(self, volume):
        vol = self.get_volume(volume)
        return vol['path']

    def run_script(self, script, host=None):
        raise Exception(
            "U must use the new method. run_script of HostSSH class"
        )
        from util import exec_remote_command_host
        output = {}
        return_code = exec_remote_command_host(
            host or self.host,
            script,
            output
        )
        if return_code != 0:
            raise EnvironmentError(
                'Could not execute script {}: {}'.format(
                    return_code, output
                )
            )
        return output

    def take_snapshot(self, persist=0):
        url = "{}snapshot/{}".format(self.base_uri, self.volume.identifier)
        if persist != 0:
            url += '?persist=1'

        LOG.info('Calling create snapshot URL: %s' % url)
        data = {
            "engine": self.engine.name,
            "db_name": self.database_name,
            "team_name": self.team_name
        }
        response = post(url, json=data, headers=self.headers)
        LOG.info('Old snapshot create status code: {}'.format(response.status_code))

        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def new_take_snapshot(self, persist=0):
        url = "{}gcp/snapshot/{}".format(self.base_uri, self.volume.identifier)
        if persist != 0:
            url += '?persist=1'

        LOG.info('Calling create snapshot URL: %s' % url)
        data = {
            "engine": self.engine.name,
            "db_name": self.database_name,
            "team_name": self.team_name
        }
        response = post(url, json=data, headers=self.headers)
        LOG.info('New snapshot create status code: {}'.format(response.status_code))

        if not response.ok:
            return response, response.content
        return response, response.json()

    def take_snapshot_status(self, identifier):
        url = "{}snapshot/{}/state".format(self.base_uri, identifier)

        LOG.info('Calling to check snapshot status. URL: %s' % url)
        response = get(url, headers=self.headers)
        LOG.info('Snapshot status status_code: {}'. format(response.status_code))

        if not response.ok:
            raise Exception(response.content, response)
        return response, response.json()

    def delete_snapshot(self, snapshot, force):
        self.force_environment = snapshot.environment

        url = "{}snapshot/{}?force={}".format(
            self.base_uri,
            snapshot.snapshopt_id,
            force
        )
        response = delete(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()['removed']

    def restore_snapshot(self, snapshot, vm_name, vm_zone, disk_offering_type):
        url = "{}snapshot/{}/restore".format(
            self.base_uri, snapshot.snapshopt_id
        )

        data = {
            'vm_name': vm_name,
            'zone': vm_zone,
            'engine': self.engine.name,
            'db_name': self.database_name,
            'team_name': self.team_name,
            'disk_offering_type': disk_offering_type
        }

        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def restore_snapshot_to_rsync(self, snapshot, vm_name, vm_zone):
        url = "{}snapshot/{}/restore-to-rsync".format(
            self.base_uri, snapshot.snapshopt_id
        )

        data = {
            'vm_name': vm_name,
            'zone': vm_zone,
            'engine': self.engine.name,
            'db_name': self.database_name,
            'team_name': self.team_name
        }

        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return True

    def add_access(self, volume, host, access_type=None):
        url = "{}access/{}".format(self.base_uri, volume.identifier)
        data = {
            "to_address": host.address
        }
        if access_type:
            data['access_type'] = access_type
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def get_snapshot_state(self, snapshot):
        url = "{}snapshot/{}/state".format(self.base_uri, snapshot.snapshopt_id)

        LOG.info('Calling to check snapshot status. URL: %s' % url)
        response = get(url, headers=self.headers)
        LOG.info('Snapshot status status_code: {}'.format(response.status_code))

        if not response.ok:
            raise VolumeProviderGetSnapshotState(response.content, response)
        return response, response.json()

    def _get_command(self, url, payload, exception_class):
        response = get(url, json=payload, headers=self.headers)
        if not response.ok:
            raise exception_class(response.content, response)
        return response.json()['command']

    def get_create_pub_key_command(self, host_ip):
        url = "{}commands/create_pub_key".format(self.base_uri)
        return self._get_command(
            url,
            {'host_ip': host_ip},
            VolumeProviderCreatePubKeyCommand
        )

    def get_remove_pub_key_command(self, host_ip):
        url = "{}commands/remove_pub_key".format(self.base_uri)
        return self._get_command(
            url,
            {'host_ip': host_ip},
            VolumeProviderRemovePubKeyCommand
        )

    def get_add_hosts_allow_command(self, host_ip):
        url = "{}commands/add_hosts_allow".format(self.base_uri)
        return self._get_command(
            url,
            {'host_ip': host_ip},
            VolumeProviderAddHostAllowCommand
        )

    def get_remove_hosts_allow_command(self, host_ip):
        url = "{}commands/remove_hosts_allow".format(self.base_uri)
        return self._get_command(
            url,
            {'host_ip': host_ip},
            VolumeProviderRemoveHostAllowCommand
        )

    def get_resize2fs_command(self, volume):
        url = "{}commands/{}/resize2fs".format(self.base_uri, volume.identifier)
        response = post(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()['command']

    def remove_access(self, volume, host):
        url = "{}access/{}/{}".format(
            self.base_uri,
            volume.identifier,
            host.address
        )
        response = delete(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def get_mount_command(self, volume, data_directory="/data", fstab=True):
        url = "{}commands/{}/mount".format(self.base_uri, volume.identifier)
        data = {
            'with_fstab': fstab,
            'data_directory': data_directory,
            'host_vm': self.host_vm.name,
            'host_zone': self.host_vm.zone
        }
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()['command']

    def get_copy_files_command(self, snapshot, source_dir, dest_dir,
                               snap_dir=''):
        # snap = volume.backups.order_by('created_at').first()
        url = "{}commands/copy_files".format(self.base_uri)
        data = {
            'snap_identifier': snapshot.snapshopt_id,
            'source_dir': source_dir,
            'dest_dir': dest_dir,
            'snap_dir': snap_dir
        }
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()['command']

    def get_scp_from_snapshot_command(self, snapshot, source_dir, dest_ip,
                                      dest_dir):
        url = "{}snapshots/{}/commands/scp".format(
            self.base_uri,
            snapshot.snapshopt_id
        )
        data = {
            'source_dir': source_dir,
            'target_ip': dest_ip,
            'target_dir': dest_dir
        }
        response = get(url, json=data, headers=self.headers)
        if not response.ok:
            raise VolumeProviderScpFromSnapshotCommand(
                response.content,
                response
            )
        return response.json()['command']

    def get_rsync_from_snapshot_command(
         self, snapshot, source_dir, dest_ip, dest_dir):
        url = "{}snapshots/{}/commands/rsync".format(
            self.base_uri,
            snapshot.snapshopt_id
        )
        data = {
            'source_dir': source_dir,
            'target_ip': dest_ip,
            'target_dir': dest_dir
        }
        response = get(url, json=data, headers=self.headers)
        if not response.ok:
            raise VolumeProviderRsyncFromSnapshotCommand(
                response.content,
                response
            )
        return response.json()['command']

    def get_umount_command(self, volume, data_directory="/data"):
        url = "{}commands/{}/umount".format(self.base_uri, volume.identifier)
        data = {
            'data_directory': data_directory
        }
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()['command']

    def clean_up(self, volume):
        url = "{}commands/{}/cleanup".format(self.base_uri, volume.identifier)
        response = get(url, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        command = response.json()['command']
        if command:
            self.host.ssh.run_script(command)

    def update_team_labels_disks(self, vm_name, team_name, zone):
        url = "{}volume/update_labels".format(self.base_uri)
        data = {
            "vm_name": str(vm_name),
            "team_name": team_name,
            "zone": str(zone)
        }
        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)
        return response.json()

    def do(self):
        raise NotImplementedError

    def undo(self):
        pass


class UpdateTeamLabelsDisks(VolumeProviderBase):
    def __unicode__(self):
        return "Updating Team Labels in All Disks..."

    def do(self):
        hostname = self.host.hostname
        hostname = hostname.replace('.globoi.com', '')
        updated = self.update_team_labels_disks(hostname, self.team_name, self.host_vm.zone)
        if not updated:
            raise EnvironmentError("Error in update Team Labels")


class VolumeProviderBaseMigrate(VolumeProviderBase):

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def environment(self):
        return self.infra.environment


class CreateVolumeDiskTypeUpgrade(VolumeProviderBase):

    def __unicode__(self):
        return "Creating Volume..."

    def _remove_volume(self, volume):
        self.destroy_volume(volume)

    @property
    def snapshot(self):
        if self.upgrade_disk_type:
            snapshot = Snapshot.objects.filter(instance=self.instance).last()
            return snapshot.snapshopt_id
        else:
            return None

    @property
    def is_valid(self):
        if self.instance.is_database and self.snapshot and self.upgrade_disk_type:
            return True
        else:
            return False

    def do(self):
        if not self.is_valid:
            return

        self.create_volume(
            self.infra.name,
            self.disk_offering.size_kb,
            self.host.address,
            snapshot_id=self.snapshot,
            is_active=False,
            zone=self.host_vm.zone,
            vm_name=self.host_vm.name,
            disk_offering_type=self.upgrade_disk_type.disk_offering_type.type
        )

    def undo(self):
        if not self.instance.is_database or not self.host:
            return

        self._remove_volume(self.latest_disk)


class NewVolume(VolumeProviderBase):

    def __unicode__(self):
        return "Creating Volume..."

    @property
    def active_volume(self):
        return True

    @property
    def is_valid(self):
        return True

    @property
    def has_snapshot_on_step_manager(self):
        return (self.host_migrate and hasattr(self, 'step_manager')
                and self.host_migrate == self.step_manager)

    def _remove_volume(self, volume, host):
        self.destroy_volume(volume)

    @property
    def restore_snapshot_from_master(self):
        return False

    @property
    def disk_offering_type(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            offering_type = self.infra.disk_offering_type.get_type_to(
                self.environment)
        else:
            offering_type = self.infra.disk_offering_type
        return offering_type.type

    def do(self):
        if not self.is_valid:
            return

        if not self.instance.is_database:
            return
        snapshot = None
        if (self.has_snapshot_on_step_manager or self.restore_snapshot_from_master):
            snapshot = self.step_manager.snapshot
        elif self.host_migrate:
            snapshot = self.host_migrate.snapshot
        elif self.base_snapshot is not None:
            snapshot = self.base_snapshot

        self.create_volume(
            self.infra.name,
            self.disk_offering.size_kb,
            self.host.address,
            snapshot_id=snapshot.snapshopt_id if snapshot else None,
            is_active=self.active_volume,
            zone=self.host_vm.zone,
            vm_name=self.host_vm.name,
            disk_offering_type=self.disk_offering_type
        )

    def undo(self):
        if not self.instance.is_database or not self.host:
            return

        for volume in self.host.volumes.all():
            self._remove_volume(volume, self.host)


class DestroyVolume(NewVolume):

    def __unicode__(self):
        return "Removing volume..."

    def do(self):
        if not self.instance.is_database or not self.host:
            return

        for volume in self.host.volumes.all():
            self._remove_volume(volume, self.host)

    def undo(self):
        return
    

class DestroyVolumeTemporaryInstance(DestroyVolume):

    def do(self):
        if not self.instance.temporary:
            return
        
        return super(DestroyVolumeTemporaryInstance, self).do()


class DestroyFirstVolume(NewVolume):

    def __unicode__(self):
        return "Removing volume..."

    def do(self):
        if not self.instance.is_database or not self.host:
            return
        self._remove_volume(self.first_disk, self.host)

    def undo(self):
        return


class NewVolumeMigrate(NewVolume):
    def __unicode__(self):
        return "Creating second volume based on snapshot for migrate..."

    @property
    def active_volume(self):
        return False

    @property
    def environment(self):
        return self.infra.environment

    @property
    def host(self):
        return self.host_migrate.host

    def undo(self):
        raise Exception("This step doesnt have roolback")


class NewVolumeFromMaster(NewVolume):
    def __unicode__(self):
        return "Restore master backup in slave..."

    @property
    def provider_class(self):
        return NewVolumeFromMaster

    @property
    def restore_snapshot_from_master(self):
        return True
    

class NewVolumeFromSnapshot(NewVolume):
    def __unicode__(self):
        return 'New Volume from last Snapshot...'
    
    @property
    def is_valid(self):
        return self.instance.temporary
    
    @property
    def provider_class(self):
        return NewVolumeFromSnapshot

    def get_base_snapshot(self):  # busca a ultima snapshot válida criada
        hosts = self.infra.hosts
        volumes = []

        for host in hosts:
            volumes.extend(host.volumes.all())

        snapshots = []

        for volume in volumes:
            snapshots.extend(volume.backups.all())

        base_snapshot = None
        for snapshot in snapshots:
            if (base_snapshot is None or snapshot.created_at > base_snapshot.created_at) and \
                    snapshot.end_at is not None and snapshot.purge_at is None:
                base_snapshot = snapshot

        if base_snapshot is None:
            raise AssertionError('Nao foi encontrada nenhuma Snapshot para criacao do novo Volume!')

        return base_snapshot

    def do(self):
        if self.is_valid:
            self.base_snapshot = self.get_base_snapshot()
            LOG.debug("New Volume usara Snapshot: %s", self.base_snapshot)
            super(NewVolumeFromSnapshot, self).do()


class NewVolumeOnSlaveMigrate(NewVolumeMigrate):
    @property
    def host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname


class NewVolumeOnSlaveMigrateFirstNode(VolumeProviderBase):
    """This class creates a new volume. This Step is only going to be executed
    during the first iteration."""

    def __init__(self, instance):
        super(NewVolumeOnSlaveMigrateFirstNode, self).__init__(instance)
        self.new_volume_step = NewVolumeOnSlaveMigrate(instance)

    def __unicode__(self):
        return str(self.new_volume_step)

    def is_first(self):
        return self.instance == self.infra.instances.first()

    def do(self):
        if self.is_first():
            self.new_volume_step.do()

    def undo(self):
        if self.is_first():
            self.new_volume_step.undo()


class RemoveVolumeMigrate(NewVolumeMigrate):
    def __unicode__(self):
        return "Removing second volume based on snapshot for migrate..."

    @property
    def host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname

    def do(self):
        vol = self.host.volumes.filter(is_active=False).last()
        if not vol:
            raise VolumeProviderRemoveVolumeMigrate(
                "Any inactive volume found"
            )
        self._remove_volume(vol, self.host)


class RemoveVolumeMigrateLastNode(VolumeProviderBase):
    """This class removes the last inactive volume. This Step is only going to
    be executed during the last iteration."""

    def __init__(self, instance):
        super(RemoveVolumeMigrateLastNode, self).__init__(instance)
        self.remove_volume_step = RemoveVolumeMigrate(instance)

    def __unicode__(self):
        return str(self.remove_volume_step)

    def is_last(self):
        return self.instance == self.infra.instances.last()

    def do(self):
        if self.is_last():
            self.remove_volume_step.do()

    def undo(self):
        if self.is_last():
            self.remove_volume_step.undo()


class NewInactiveVolume(NewVolume):
    def __unicode__(self):
        return "Creating Inactive Volume..."

    @property
    def active_volume(self):
        return False


class MountDataVolume(VolumeProviderBase):

    def __unicode__(self):
        return "Mounting {} volume...".format(self.directory)

    @property
    def directory(self):
        return "/data"

    @property
    def is_valid(self):
        return self.instance.is_database

    def do(self):
        if not self.is_valid:
            return

        script = self.get_mount_command(self.volume)
        # self.run_script(script)
        self.host.ssh.run_script(script)

    def undo(self):
        pass

class MountDataVolumeTemporaryInstance(MountDataVolume):
    @property
    def is_valid(self):
        if not self.instance.temporary:
            return False
        return super(MountDataVolumeTemporaryInstance, self).is_valid
    
    def do(self):
        if not self.is_valid:
            return
        
        super(MountDataVolumeTemporaryInstance, self).do()


class MountDataVolumeUpgradeDiskType(MountDataVolume):

    def __unicode__(self):
        return "Mounting {} volume...".format(self.directory)

    def do(self):
        if not self.is_valid:
            return

        script = self.get_mount_command(self.latest_disk)
        self.host.ssh.run_script(script)

    def undo(self):
        if not self.is_valid:
            return

        script = self.get_umount_command(self.latest_disk)
        if script:
            self.host.ssh.run_script(script)


class MountDataNewVolume(MountDataVolume):
    @property
    def is_valid(self):
        return True

    @property
    def volume(self):
        return self.latest_disk


class MountDataLatestVolume(MountDataVolume):

    def __unicode__(self):
        return "Mounting new volume on {} for copy...".format(self.directory)

    @property
    def directory(self):
        return "/data_latest_volume"

    def do(self):
        if not self.is_valid:
            return

        script = self.get_mount_command(
            self.latest_disk,
            data_directory=self.directory,
            fstab=False
        )
        # self.run_script(script)
        self.host.ssh.run_script(script)

    def undo(self):
        script = self.get_umount_command(
            self.latest_disk,
            data_directory=self.directory,
        )
        # self.run_script(script)
        self.host.ssh.run_script(script)


class UnmountDataLatestVolume(MountDataLatestVolume):

    def __unicode__(self):
        return "Umounting new volume on {} for copy...".format(self.directory)

    def do(self):
        return super(UnmountDataLatestVolume, self).undo()

    def undo(self):
        return super(UnmountDataLatestVolume, self).do()


class MountDataVolumeMigrate(MountDataVolume):

    def __unicode__(self):
        return "Mounting old volume in new instance on dir {}...".format(
            self.directory
        )

    @property
    def directory(self):
        return "/data_migrate"

    @property
    def host_migrate_volume(self):
        return self.host_migrate.host.volumes.get(is_active=True)

    @property
    def environment(self):
        return self.infra.environment

    def do(self):
        script = self.get_mount_command(
            self.host_migrate_volume,
            data_directory=self.directory,
            fstab=False
        )
        # self.run_script(script)
        self.host.ssh.run_script(script)

    def undo(self):
        script = self.get_umount_command(
            self.host_migrate_volume,
            data_directory=self.directory,
        )
        # self.run_script(script)
        self.host.ssh.run_script(script)


class MountDataVolumeRecreateSlave(MountDataVolumeMigrate):

    def __unicode__(self):
        return "Mounting master volume in slave instance on dir {}...".format(
            self.directory
        )

    @property
    def directory(self):
        return "/data_recreate_slave"

    @property
    def host_migrate_volume(self):
        master_instance = self.infra.get_driver().get_master_instance()
        return master_instance.hostname.volumes.get(is_active=True)

    def do(self):
        if self.is_database_instance:
            super(MountDataVolumeRecreateSlave, self).do()

    def undo(self):
        if self.is_database_instance:
            super(MountDataVolumeRecreateSlave, self).undo()


class UmountDataVolumeRecreateSlave(MountDataVolumeRecreateSlave):

    def __unicode__(self):
        return "Umounting master volume in slave instance on dir {}...".format(
            self.directory
        )

    def do(self):
        if self.is_database_instance:
            super(UmountDataVolumeRecreateSlave, self).undo()

    def undo(self):
        if self.is_database_instance:
            super(UmountDataVolumeRecreateSlave, self).do()


class MountDataVolumeDatabaseMigrate(MountDataVolumeMigrate):
    def __unicode__(self):
        return "Mounting new volume for scp {}...".format(self.directory)

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def host_migrate_volume(self):
        return self.host.volumes.filter(is_active=False).last()


class MountDataVolumeOnSlaveMigrate(MountDataVolumeDatabaseMigrate):
    @property
    def host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname


class MountDataVolumeOnSlaveFirstNode(VolumeProviderBase):
    """This class executes volume mounting on the slave instance. This Step is
    only going to be executed during the first iteration."""

    def __init__(self, instance):
        super(MountDataVolumeOnSlaveFirstNode, self).__init__(instance)
        self.mount_step = MountDataVolumeOnSlaveMigrate(instance)

    def __unicode__(self):
        return str(self.mount_step)

    def is_first(self):
        return self.instance == self.infra.instances.first()

    def do(self):
        if self.is_first():
            self.mount_step.do()

    def undo(self):
        if self.is_first():
            self.mount_step.undo()


class UmountDataVolumeDatabaseMigrate(MountDataVolumeDatabaseMigrate):
    def __unicode__(self):
        return "Umounting new volume for scp {}...".format(self.directory)

    def do(self):
        return super(UmountDataVolumeDatabaseMigrate, self).undo()

    def undo(self):
        return super(UmountDataVolumeDatabaseMigrate, self).do()


class UmountDataVolumeOnSlaveMigrate(UmountDataVolumeDatabaseMigrate):
    @property
    def host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname


class UmountDataVolumeOnSlaveLastNode(VolumeProviderBase):
    """This class executes volume unmounting on the slave instance. This Step
    is only going to be executed during the last iteration."""

    def __init__(self, instance):
        super(UmountDataVolumeOnSlaveLastNode, self).__init__(instance)
        self.umount_step = UmountDataVolumeOnSlaveMigrate(instance)

    def __unicode__(self):
        return str(self.umount_step)

    def is_last(self):
        return self.instance == self.infra.instances.last()

    def do(self):
        if self.is_last():
            self.umount_step.do()

    def undo(self):
        if self.is_last():
            self.umount_step.undo()


class UmountDataVolumeMigrate(MountDataVolumeMigrate):

    def __unicode__(self):
        return "Dismounting old volume in new instance on dir {}...".format(
            self.directory
        )

    def do(self):
        return super(UmountDataVolumeMigrate, self).undo()

    def undo(self):
        return super(UmountDataVolumeMigrate, self).do()


class TakeSnapshotForSecondaryOrReadOnly(VolumeProviderBase):

    def __unicode__(self):
        return "Taking Snapshot for Secondary or ReadOnly Host..."

    @property
    def is_valid(self):
        return self.instance.temporary

    @property
    def provider_class(self):
        return VolumeProviderBase

    def instance_for_backup(self):
        read_only_instance = self.database.infra.instances.filter(read_only=True).first()
        if read_only_instance:
            return read_only_instance

        instances = self.database.infra.instances.all()
        driver = instances[0].databaseinfra.get_driver()

        for instance in instances:
            if not driver.check_instance_is_master(instance):
                return instance

        raise Exception("Nao foi encontrada instance secundaria ou ReadOnly")

    def do(self):
        if not self.is_valid:
            return

        from backup.tasks import make_instance_snapshot_backup
        from backup.models import BackupGroup

        group = BackupGroup()
        group.save()

        instance = self.instance_for_backup()
        LOG.debug("Instance for backup: %s", instance)

        snapshot = make_instance_snapshot_backup(
            instance,
            {},
            group,
            provider_class=self.provider_class,
            target_volume=None
        )

        if not snapshot:
            raise VolumeProviderSnapshotNotFoundError(
                'Backup was unsuccessful in {}'.format(self.instance)
            )

        snapshot.is_automatic = False
        snapshot.save()

        if snapshot.has_warning:
            raise VolumeProviderSnapshotHasWarningStatusError(
                'Backup was warning'
            )

        if snapshot.was_error:
            error = 'Backup was unsuccessful.'
            if snapshot.error:
                error = '{} Error: {}'.format(error, snapshot.error)
                raise VolumeProviderSnapshotHasErrorStatus(error)


class TakeSnapshotMigrate(VolumeProviderBase):

    def __init__(self, *args, **kw):
        super(TakeSnapshotMigrate, self).__init__(*args, **kw)
        self._database_migrate = None

    def __unicode__(self):
        return "Doing backup for copy..."

    @property
    def provider_class(self):
        return VolumeProviderBaseMigrate

    @property
    def is_database_migrate(self):
        return self.host_migrate and self.host_migrate.database_migrate

    @property
    def database_migrate(self):
        if self._database_migrate:
            return self._database_migrate
        self._database_migrate = (self.host_migrate and
                                  self.host_migrate.database_migrate)
        return self._database_migrate

    @property
    def target_volume(self):
        return None

    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def only_once(self):
        return True

    def do(self):

        if not self.is_valid:
            return

        from backup.tasks import make_instance_snapshot_backup
        from backup.models import BackupGroup
        if (self.only_once and
            self.database_migrate and
            self.database_migrate.host_migrate_snapshot):
            snapshot = self.database_migrate.host_migrate_snapshot
        else:
            group = BackupGroup()
            group.save()
            snapshot = make_instance_snapshot_backup(
                self.instance,
                {},
                group,
                provider_class=self.provider_class,
                target_volume=self.target_volume
            )

            if not snapshot:
                raise VolumeProviderSnapshotNotFoundError(
                    'Backup was unsuccessful in {}'.format(self.instance)
                )

            snapshot.is_automatic = False
            snapshot.save()

            if snapshot.has_warning:
                raise VolumeProviderSnapshotHasWarningStatusError(
                    'Backup was warning'
                )

            if snapshot.was_error:
                error = 'Backup was unsuccessful.'
                if snapshot.error:
                    error = '{} Error: {}'.format(error, snapshot.error)
                    raise VolumeProviderSnapshotHasErrorStatus(error)

        if self.database_migrate:
            host_migrate = self.host_migrate
            host_migrate.snapshot = snapshot
            host_migrate.save()
        else:
            self.step_manager.snapshot = snapshot
            self.step_manager.save()

        if snapshot.has_warning:
            raise VolumeProviderSnapshotHasWarningStatusError(
                'Backup was warning'
            )

    def undo(self):
        pass


class TakeSnapshotMigrateAllInstances(TakeSnapshotMigrate):

    @property
    def only_once(self):
        return False

    '''
    def do(self):
        if not self.is_valid:
            return

        from backup.tasks import make_instance_snapshot_backup
        from backup.models import BackupGroup

        group = BackupGroup()
        group.save()
        snapshot = make_instance_snapshot_backup(
            self.instance,
            {},
            group,
            provider_class=self.provider_class,
            target_volume=self.target_volume
        )

        if not snapshot:
            raise VolumeProviderSnapshotNotFoundError(
                'Backup was unsuccessful in {}'.format(
                    self.instance)
            )

        snapshot.is_automatic = False
        snapshot.save()

        if snapshot.has_warning:
            raise VolumeProviderSnapshotHasWarningStatusError(
                'Backup was warning'
            )

        if snapshot.was_error:
            error = 'Backup was unsuccessful.'
            if snapshot.error:
                error = '{} Error: {}'.format(error, snapshot.error)
                raise VolumeProviderSnapshotHasErrorStatus(error)

        if self.database_migrate:
            host_migrate = self.host_migrate
            host_migrate.snapshot = snapshot
            host_migrate.save()
        else:
            self.step_manager.snapshot = snapshot
            self.step_manager.save()

        if snapshot.has_warning:
            raise VolumeProviderSnapshotHasWarningStatusError(
                'Backup was warning'
            )
    '''


class TakeSnapshotFromMaster(TakeSnapshotMigrate):
    def __unicode__(self):
        return "Doing backup from master..."

    @property
    def provider_class(self):
        return TakeSnapshotFromMaster

    @property
    def target_volume(self):
        return self.volume

    @property
    def host(self):
        return self.instance.hostname

    @property
    def group(self):
        from backup.models import BackupGroup
        group = BackupGroup()
        group.save()
        return group

    def do(self):
        if self.is_database_instance:
            driver = self.infra.get_driver()
            self.instance = driver.get_master_instance()
            super(TakeSnapshotFromMaster, self).do()


class RemoveSnapshotMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Removing backup used on migrate..."

    @property
    def environment(self):
        return self.infra.environment

    def do(self):
        from backup.tasks import remove_snapshot_backup
        if self.is_database_instance:
            if self.host_migrate and self.host_migrate.database_migrate:
                snapshot = self.host_migrate.snapshot
            else:
                snapshot = self.step_manager.snapshot
            if not snapshot:
                raise VolumeProviderRemoveSnapshotMigrate(
                    'No snapshot found on {} instance for migration'.format(
                        self.step_manager
                    )
                )
            remove_snapshot_backup(snapshot, self, force=1)

    def undo(self):
        pass


class CopyFilesMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Copying data to {} from {}...".format(
            self.source_directory,
            self.dest_directory
        )

    @property
    def source_directory(self):
        return "/data_migrate"

    @property
    def dest_directory(self):
        return "/data"

    @property
    def snap_dir(self):
        return ""

    def do(self):
        script = self.get_copy_files_command(
            self.step_manager.snapshot,
            self.source_directory,
            self.dest_directory,
            self.snap_dir
        )
        self.host.ssh.run_script(script)

    def undo(self):
        pass


class CopyDataFromSnapShot(CopyFilesMigrate):

    def __unicode__(self):
        return "Copying data to snapshot to {}...".format(
            self.dest_directory
        )

    @property
    def source_directory(self):
        return "/data_recreate_slave"

    @property
    def dest_directory(self):
        return "/data/data"

    @property
    def snap_dir(self):
        return "data/"

    def do(self):
        if self.is_database_instance:
            super(CopyDataFromSnapShot, self).do()


class CopyReplFromSnapShot(CopyDataFromSnapShot):

    def __unicode__(self):
        return "Copying repl to snapshot to {}...".format(
            self.dest_directory
        )

    @property
    def dest_directory(self):
        return "/data/repl"

    @property
    def snap_dir(self):
        return "repl/"


class CopyFiles(VolumeProviderBase):

    def __unicode__(self):
        return "Copying data to {} from {}...".format(
            self.source_directory,
            self.dest_directory
        )

    @property
    def source_directory(self):
        return "/data"

    @property
    def dest_directory(self):
        return "/data_latest_volume"

    def do(self):
        script = "cp -rp {}/* {}".format(
            self.source_directory,
            self.dest_directory
        )
        self.host.ssh.run_script(script)

    def undo(self):
        pass


class CopyPermissions(VolumeProviderBase):

    def __unicode__(self):
        return "Copying permissions from {} to {}...".format(
            self.source_directory,
            self.dest_directory
        )

    @property
    def source_directory(self):
        return "/data"

    @property
    def dest_directory(self):
        return "/data_latest_volume"

    def do(self):
        script = ('stat -c "%a" {0} | xargs -I{{}} chmod {{}} {1}'
                  ' && stat -c "%U:%G" {0} '
                  '| xargs -I{{}} chown {{}} {1}').format(
                    self.source_directory, self.dest_directory)
        self.host.ssh.run_script(script)


class ScpFromSnapshotMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Copying data from snapshot to new host..."

    @property
    def source_dir(self):
        return "/data"

    @property
    def dest_dir(self):
        return "/data"

    @property
    def environment(self):
        return self.infra.environment

    @property
    def host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname

    def do(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            snapshot = self.host_migrate.snapshot
        else:
            snapshot = self.step_manager.snapshot

        script = self.get_scp_from_snapshot_command(
            snapshot,
            self.source_dir,
            self.host_migrate.host.future_host.address,
            self.dest_dir
        )
        self.host.ssh.run_script(script)

    def undo(self):
        pass


class ScpFromSnapshotDatabaseMigrate(ScpFromSnapshotMigrate):

    @property
    def source_dir(self):
        return "/data_migrate"


class MountDataVolumeRestored(MountDataVolume):

    @property
    def is_valid(self):
        if not super(MountDataVolumeRestored, self).is_valid:
            return False

        return self.restore.is_master(self.instance)

    @property
    def volume(self):
        return self.latest_disk


class UnmountActiveVolume(VolumeProviderBase):

    def __unicode__(self):
        return "Umounting {} volume...".format(self.directory)

    @property
    def directory(self):
        return "/data"

    @property
    def is_valid(self):
        return self.restore.is_master(self.instance)

    def do(self):
        if not self.is_valid:
            return

        script = self.get_umount_command(self.volume)
        if script:
            self.host.ssh.run_script(script)

    def undo(self):
        pass


class UnmountActiveVolumeUpgradeDiskType(UnmountActiveVolume):
    @property
    def is_valid(self):
        return self.is_database_instance

    def do(self):
        if not self.is_valid:
            return

        script = self.get_umount_command(self.volume)
        if script:
            self.host.ssh.run_script(script)

    def undo(self):
        if not self.is_valid:
            return

        script = self.get_mount_command(self.volume)
        if script:
            self.host.ssh.run_script(script)


class UnmountDataVolume(UnmountActiveVolume):
    @property
    def is_valid(self):
        return True


class ResizeVolumeBase(VolumeProviderBase):
    @property
    def environment(self):

        if not self.migration_in_progress:
            return super(ResizeVolumeBase, self).environment

        migration = DatabaseMigrate.objects.filter(
                     database=self.database).last()
        if self.instance.future_instance:
            return migration.origin_environment
        else:
            return migration.environment


class ResizeVolume(ResizeVolumeBase):
    def __unicode__(self):
        return "Resizing data volume..."

    def do(self):
        if not self.instance.is_database:
            return

        url = "{}resize/{}".format(self.base_uri, self.volume.identifier)
        data = {
            "new_size_kb": self.infra.disk_offering.size_kb,
        }

        response = post(url, json=data, headers=self.headers)
        if not response.ok:
            raise IndexError(response.content, response)

        volume = self.volume
        volume.total_size_kb = self.infra.disk_offering.size_kb
        volume.save()

    def undo(self):
        pass


class RestoreSnapshot(VolumeProviderBase):

    def __unicode__(self):
        if not self.snapshot:
            return "Skipping restoring (No snapshot for this instance)..."

        return "Restoring {}...".format(self.snapshot)

    @property
    def disk_host(self):
        return self.restore.master_for(self.instance).hostname

    @property
    def vm_info(self):
        return self.host_prov_client.get_vm_by_host(self.disk_host)

    @property
    def vm_name(self):
        return self.vm_info.name

    @property
    def vm_zone(self):
        return self.vm_info.zone

    def do(self):
        snapshot = self.snapshot
        if not snapshot:
            return
        response = self.restore_snapshot(
            snapshot,
            self.vm_name,
            self.vm_zone,
            self.infra.disk_offering_type.type,
        )

        volume = self.latest_disk
        volume.identifier = response['identifier']
        volume.is_active = False
        volume.id = None
        volume.host = self.disk_host
        volume.disk_offering_type = self.infra.disk_offering_type.type
        volume.save()

    def undo(self):
        if not self.snapshot:
            return

        self.destroy_volume(self.latest_disk)


class RestoreSnapshotToMaster(RestoreSnapshot):
    @property
    def vm_name(self):
        return self.master_host_vm.name

    @property
    def vm_zone(self):
        return self.master_host_vm.zone


class AddAccess(VolumeProviderBase):

    @property
    def disk_time(self):
        raise NotImplementedError

    @property
    def volume(self):
        raise NotImplementedError

    def __unicode__(self):
        return "Adding permission to {} disk ...".format(self.disk_time)

    def do(self):
        if not self.is_valid:
            return
        self.add_access(self.volume, self.host)


class AddAccessUpgradedDiskTypeVolume(AddAccess):
    @property
    def disk_time(self):
        return "restored"

    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def volume(self):
        return self.latest_disk


class AddAccessRestoredVolume(AddAccess):

    @property
    def disk_time(self):
        return "restored"

    @property
    def is_valid(self):
        return self.restore.is_master(self.instance)

    @property
    def volume(self):
        return self.latest_disk


class AddAccessNewVolume(AddAccess):

    @property
    def disk_time(self):
        return "new"

    @property
    def is_valid(self):
        return True

    @property
    def volume(self):
        return self.latest_disk


class AddAccessMigrate(AddAccess):
    def __unicode__(self):
        return "Adding permission to old disk..."

    @property
    def volume(self):
        return self.host_migrate.host.volumes.get(is_active=True)

    @property
    def environment(self):
        return self.infra.environment

    def undo(self):
        self.remove_access(self.volume, self.host)


class AddAccessRecreateSlave(AddAccess):
    def __unicode__(self):
        return "Adding permission to old disk..."

    @property
    def volume(self):
        master_instance = self.infra.get_driver().get_master_instance()
        return master_instance.hostname.volumes.get(is_active=True)

    def do(self):
        if not self.is_valid or not self.is_database_instance:
            return
        self.add_access(self.volume, self.host, 'read-only')

    def undo(self):
        if self.is_database_instance:
            self.remove_access(self.volume, self.host)


class RemoveAccessRecreateSlave(AddAccessRecreateSlave):
    def __unicode__(self):
        return "Removing permission to old master disk..."

    def do(self):
        if self.is_database_instance:
            super(RemoveAccessRecreateSlave, self).undo()

    def undo(self):
        if self.is_database_instance:
            super(RemoveAccessRecreateSlave, self).do()


class RemoveAccessMigrate(AddAccessMigrate):
    def __unicode__(self):
        return "Removing permission to old disk..."

    def do(self):
        return super(RemoveAccessMigrate, self).undo()

    def undo(self):
        return super(RemoveAccessMigrate, self).do()


class AddHostsAllowMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Adding network on hosts_allow file..."

    @property
    def original_host(self):
        return self.host_migrate.host

    @property
    def is_valid(self):
        return self.is_database_instance

    def _do_hosts_allow(self, func):
        script = func(
            self.original_host.address,
        )

        self.host.ssh.run_script(script)

    def add_hosts_allow(self):
        self._do_hosts_allow(
            self.get_add_hosts_allow_command
        )

    def remove_hosts_allow(self):
        self._do_hosts_allow(
            self.get_remove_hosts_allow_command
        )

    def do(self):
        if not self.is_valid:
            return
        self.add_hosts_allow()

    def undo(self):
        if not self.is_valid:
            return
        self.remove_hosts_allow()


class AddHostsAllowDatabaseMigrate(AddHostsAllowMigrate):
    @property
    def original_host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname


class AddHostsAllowMigrateBackupHost(AddHostsAllowMigrate):
    @property
    def snapshot(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            return self.host_migrate.database_migrate.host_migrate_snapshot
        else:
            return self.step_manager.snapshot

    @property
    def original_host(self):
        return self.snapshot.instance.hostname


class CreatePubKeyMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Creating pubblic key..."

    @property
    def original_host(self):
        return self.host_migrate.host

    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def environment(self):
        return self.infra.environment

    def _do_pub_key(self, func):
        script = func(self.original_host.address)
        return self.original_host.ssh.run_script(script)

    def create_pub_key(self):
        output = self._do_pub_key(self.get_create_pub_key_command)
        pub_key = output['stdout'][0]
        script = 'echo "{}" >> ~/.ssh/authorized_keys'.format(pub_key)
        self.host.ssh.run_script(script)

    def remove_pub_key(self):
        self._do_pub_key(self.get_remove_pub_key_command)

    def do(self):
        if not self.is_valid:
            return
        self.create_pub_key()

    def undo(self):
        if not self.is_valid:
            return
        self.remove_pub_key()


class CreatePubKeyMigrateBackupHost(CreatePubKeyMigrate):
    @property
    def snapshot(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            return self.host_migrate.database_migrate.host_migrate_snapshot
        else:
            return self.step_manager.snapshot

    @property
    def original_host(self):
        return self.snapshot.instance.hostname


class RemovePubKeyMigrate(CreatePubKeyMigrate):

    def __unicode__(self):
        return "Removing pubblic key..."

    def do(self):
        self.remove_pub_key()

    def undo(self):
        self.create_pub_key()

class RemovePubKeyMigrateHostMigrate(RemovePubKeyMigrate, CreatePubKeyMigrateBackupHost):
    pass


class RemoveHostsAllowMigrate(AddHostsAllowMigrate):

    def __unicode__(self):
        return "Removing network from hosts_allow file..."

    def do(self):
        self.remove_hosts_allow()

    def undo(self):
        self.add_hosts_allow()

class RemoveHostsAllowMigrateBackupHost(RemoveHostsAllowMigrate, AddHostsAllowMigrateBackupHost):
    pass


class RemoveHostsAllowDatabaseMigrate(RemoveHostsAllowMigrate):
    @property
    def original_host(self):
        master_instance = self.driver.get_master_instance()
        return self.infra.instances.exclude(
            id=master_instance.id
        ).first().hostname


class TakeSnapshotUpgradeDiskType(VolumeProviderBase):
    def __unicode__(self):
        return "Doing backup of old data to upgrade disk..."

    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def provider_class(self):
        return VolumeProviderBase

    @property
    def target_volume(self):
        return self.volume

    @property
    def group(self):
        from backup.models import BackupGroup
        group = BackupGroup()
        group.save()
        return group

    def do(self):
        if not self.is_valid:
            return

        from backup.tasks import make_instance_snapshot_backup_upgrade_disk
        from backup.models import BackupGroup

        group = BackupGroup()
        group.save()
        snapshot = make_instance_snapshot_backup_upgrade_disk(
            self.instance,
            {},
            group,
            provider_class=self.provider_class,
            target_volume=self.target_volume
        )

        if not snapshot:
            raise VolumeProviderSnapshotNotFoundError(
                'Backup was unsuccessful in {}'.format(
                    self.instance)
            )

        snapshot.is_automatic = False
        snapshot.save()

        if snapshot.has_warning:
            raise VolumeProviderSnapshotHasWarningStatusError(
                'Backup was warning'
            )

        if snapshot.was_error:
            error = 'Backup was unsuccessful.'
            if snapshot.error:
                error = '{} Error: {}'.format(error, snapshot.error)
                raise VolumeProviderSnapshotHasErrorStatus(error)

    def undo(self):
        pass


class TakeSnapshot(VolumeProviderBase):
    def __unicode__(self):
        return "Doing backup of old data..."

    @property
    def is_valid(self):
        return self.restore.is_master(self.instance)

    @property
    def group(self):
        return self.restore.new_group

    def do(self):
        if not self.is_valid:
            return

        snapshot = Snapshot.create(self.instance, self.group, self.volume)
        response = self.take_snapshot()
        snapshot.done(response)
        snapshot.status = Snapshot.SUCCESS
        snapshot.end_at = datetime.now()
        snapshot.save()

    def undo(self):
        pass


class TakeSnapshotOldDisk(TakeSnapshot):

    @property
    def is_valid(self):
        return True

    @property
    def group(self):
        from backup.models import BackupGroup
        group = BackupGroup()
        group.save()
        return group


class WaitSnapshotAvailableMigrate(VolumeProviderBase):
    #TODO: colocar estas variáveis no .env OU no Configuration
    ATTEMPTS = 60
    DELAY = 5

    def __unicode__(self):
        return "Wait snapshot available..."

    @property
    def environment(self):
        return self.infra.environment

    @property
    def is_valid(self):
        return self.is_database_instance

    def waiting_be(self, state, snapshot):
        for _ in range(self.ATTEMPTS):
            response, snapshot_state = self.get_snapshot_state(snapshot)
            if snapshot_state['snapshot_status'] == state:
                return True
            sleep(self.DELAY)
        raise EnvironmentError("Snapshot {} is {} should be {}".format(
            snapshot, state, snapshot_state
        ))

    @property
    def snapshot(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            return self.host_migrate.database_migrate.host_migrate_snapshot
        else:
            return self.step_manager.snapshot

    def do(self):
        if not self.is_valid:
            return
        # Solucao de contorno para resolver recreate slave DCCM
        if self.environment.name == 'prod':
            return

        self.waiting_be('READY', self.snapshot)


class UpdateActiveDisk(VolumeProviderBase):

    def __unicode__(self):
        return "Updating meta data..."

    def do(self):
        if not self.instance.is_database:
            return

        old_disk = self.volume
        new_disk = self.latest_disk
        if old_disk != new_disk:
            old_disk.is_active = False
            new_disk.is_active = True
            old_disk.save()
            new_disk.save()

    def undo(self):
        pass


class UpdateActiveDiskTypeUpgrade(VolumeProviderBase):

    def __unicode__(self):
        return "Updating meta data..."

    @property
    def new_disk(self):
        return self.latest_disk

    @property
    def old_disk(self):
        return self.host.volumes.filter(is_active=True).first()

    def do(self):
        if not self.instance.is_database:
            return

        old_disk = self.old_disk
        new_disk = self.new_disk
        if old_disk != new_disk:
            old_disk.is_active = False
            new_disk.is_active = True
            old_disk.save()
            new_disk.save()

    def undo(self):
        if not self.instance.is_database:
            return

        old_disk = self.new_disk
        new_disk = self.old_disk
        if old_disk != new_disk:
            old_disk.is_active = False
            new_disk.is_active = True
            old_disk.save()
            new_disk.save()


class DestroyOldEnvironment(VolumeProviderBase):

    def __unicode__(self):
        return "Removing old backups and volumes..."

    @property
    def environment(self):
        if self.force_environment is not None:
            return self.force_environment

        return self.infra.environment

    @property
    def credential(self):
        if self.force_environment is not None:
            return self.credential_by_env(self.force_environment)

        return super(DestroyOldEnvironment, self).credential

    @property
    def host(self):
        return self.instance.hostname

    @property
    def is_valid(self):
        return not self.should_migrate_with_new_disk

    @property
    def can_run(self):
        if not self.instance.is_database:
            return False
        if not self.host_migrate.database_migrate:
            return False
        return super(DestroyOldEnvironment, self).can_run

    def do(self):
        if not self.is_valid:
            return

        for volume in self.host.volumes.all():
            self.destroy_volume(volume)

    def undo(self):
        raise NotImplementedError


class DetachDataVolume(VolumeProviderBase):
    def __unicode__(self):
        return "Detaching disk from VM..."

    @property
    def is_valid(self):
        return self.instance.is_database

    def do(self):
        if not self.is_valid:
            return

        for volume in self.host.volumes.all():
            self.detach_disk(volume)

    def undo(self):
        if not self.is_valid:
            return

        if hasattr(self, 'host_migrate'):
            AttachDataVolume(self.instance).do()
            MountDataVolume(self.instance).do()

    
class DetachDataVolumeTemporaryInstance(DetachDataVolume):

    @property
    def is_valid(self):
        return self.instance.is_database and self.instance.temporary


class DetachFirstVolume(VolumeProviderBase):
    def __unicode__(self):
        return "Detaching first disk from VM..."

    @property
    def is_valid(self):
        return self.instance.is_database

    def do(self):
        if not self.is_valid:
            return
        self.detach_disk(self.first_disk)

    def undo(self):
        if not self.is_valid:
            return

        if hasattr(self, 'host_migrate'):
            AttachDataVolume(self.instance).do()


class DetachActiveVolume(DetachDataVolume):

    def __unicode__(self):
        return "Detaching volume..."

    @property
    def is_valid(self):
        if not super(DetachActiveVolume, self).is_valid:
            return False

        return self.restore.is_master(self.instance)

    def do(self):
        if not self.is_valid:
            return

        self.detach_disk(self.volume)

    def undo(self):
        pass


class MoveDisk(VolumeProviderBase):
    def __unicode__(self):
        return "Moving disk..."

    @property
    def is_valid(self):
        return self.instance.is_database

    @property
    def zone(self):
        return self.host_migrate.zone

    @property
    def volume(self):
        return self.volume_migrate

    def do(self):
        if not self.is_valid:
            return

        self.move_disk(self.volume, self.zone)

    def undo(self):
        if not self.is_valid:
            return

        self.move_disk(self.volume, self.host_migrate.zone_origin)


class MoveDiskRestore(MoveDisk):

    def is_valid(self):
        if not super(MoveDiskRestore, self).is_valid:
            return False

        return self.restore.is_master(self.instance)

    @property
    def zone(self):
        return self.host_vm.zone

    @property
    def volume(self):
        return self.latest_disk


class MountDataVolumeWithUndo(MountDataVolume):

    def undo(self):
        if not self.is_valid:
            return

        UnmountDataVolume(self.instance).do()


class Resize2fs(ResizeVolumeBase):
    def __unicode__(self):
        return "Resizing data volume to file system..."

    def do(self):
        if not self.instance.is_database:
            return

        script = self.get_resize2fs_command(self.volume)
        if script:
            self.host.ssh.run_script(script)

    def undo(self):
        pass


class AttachDataVolume(VolumeProviderBase):
    def __unicode__(self):
        return "Attach disk in VM..."

    @property
    def is_valid(self):
        return self.instance.is_database

    def do(self):
        if not self.is_valid:
            return

        self.attach_disk(self.volume)

    def undo(self):
        if not self.is_valid:
            return
        self.detach_disk(self.volume)


class AttachDataVolumeTemporaryInstance(AttachDataVolume):
    @property
    def is_valid(self):
        if not self.instance.temporary:
            return False
        return super(AttachDataVolumeTemporaryInstance, self).is_valid

    def do(self):
        if not self.is_valid:
            return

        super(AttachDataVolumeTemporaryInstance, self).do()


class AttachDataVolumeUpgradeDiskType(VolumeProviderBase):
    def __unicode__(self):
        return "Attach disk in VM..."

    @property
    def is_valid(self):
        return self.instance.is_database

    def do(self):
        if not self.is_valid:
            return

        self.attach_disk(self.latest_disk)

    def undo(self):
        if not self.is_valid:
            return
        self.detach_disk(self.latest_disk)


class AttachDataVolumeWithUndo(AttachDataVolume):
    def undo(self):
        DetachDataVolume(self.instance).do()


class AttachDataVolumeRestored(AttachDataVolume):

    @property
    def is_valid(self):
        if not super(AttachDataVolumeRestored, self).is_valid:
            return False

        return self.restore.is_master(self.instance)

    @property
    def volume(self):
        return self.latest_disk


class AttachDataVolumeMigrate(AttachDataVolume):

    @property
    def host_migrate_volume(self):
        return self.host_migrate.host.volumes.get(is_active=True)

    def do(self):
        self.attach_disk(self.host_migrate_volume)

    def undo(self):
        self.detach_disk(self.host_migrate_volume)


class AttachDataVolumeRecreateSlave(AttachDataVolumeMigrate):

    @property
    def host_migrate_volume(self):
        master_instance = self.infra.get_driver().get_master_instance()
        return master_instance.hostname.volumes.get(is_active=True)

    def do(self):
        if self.is_database_instance:
            super(AttachDataVolumeRecreateSlave, self).do()

    def undo(self):
        if self.is_database_instance:
            super(AttachDataVolumeRecreateSlave, self).undo()


class DetachDataVolumeMigrate(AttachDataVolumeMigrate):

    def __unicode__(self):
        return "Detach old volume in new instance..."

    def do(self):
        return super(DetachDataVolumeMigrate, self).undo()

    def undo(self):
        return super(DetachDataVolumeMigrate, self).do()


class DetachDataVolumeRecreateSlave(AttachDataVolumeRecreateSlave):

    def __unicode__(self):
        return "Detach master volume in slave instance..."

    def do(self):
        if self.is_database_instance:
            super(DetachDataVolumeRecreateSlave, self).undo()

    def undo(self):
        if self.is_database_instance:
            super(DetachDataVolumeRecreateSlave, self).do()


class RsyncFromSnapshotMigrate(VolumeProviderBase):

    def __unicode__(self):
        return "Copying (rsync) data from snapshot to new host..."

    @property
    def source_root_restore(self):
        if self.should_migrate_with_new_disk:
            return "/data_latest_volume"
        return "/data"

    @property
    def source_dir(self):
        return self.source_root_restore

    @property
    def dest_dir(self):
        return "/data"

    @property
    def environment(self):
        return self.infra.environment

    @property
    def host(self):
        return self.instance.hostname

    def do(self):
        if not self.is_valid:
            return

        if self.host_migrate and self.host_migrate.database_migrate:
            snapshot = self.host_migrate.snapshot
        else:
            snapshot = self.step_manager.snapshot

        script = self.get_rsync_from_snapshot_command(
            snapshot,
            self.source_dir,
            self.host_migrate.host.future_host.address,
            self.dest_dir
        )

        self.host.ssh.run_script(script)

    def undo(self):
        pass


class RsyncFromSnapshotMigrateBackupHost(RsyncFromSnapshotMigrate):
    def __unicode__(self):
        return "Copying (rsync) from snapshot to new host..."

    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def snapshot(self):
        if self.host_migrate and self.host_migrate.database_migrate:
            return self.host_migrate.database_migrate.host_migrate_snapshot
        else:
            return self.step_manager.snapshot

    @property
    def host(self):
        if self.is_database_instance:
            return self.snapshot.instance.hostname

    @property
    def source_root_restore(self):
        if not self.is_database_instance:
            return "/data"

        return super(RsyncFromSnapshotMigrateBackupHost, self)\
            .source_root_restore


class RsyncDataFromSnapshotMigrateBackupHost(RsyncFromSnapshotMigrateBackupHost):
    @property
    def is_valid(self):
        return self.is_database_instance

    @property
    def source_dir(self):
        return "{}/data".format(self.source_root_restore)

    @property
    def dest_dir(self):
        return "/data/data"


class VolumeProviderSnapshot(VolumeProviderBase):

    @property
    def environment(self):
        if self.force_environment is not None:
            return self.force_environment

        if not self.migration_in_progress:
            return super(VolumeProviderSnapshot, self).environment

        migration = DatabaseMigrate.objects.filter(
                     database=self.database).last()
        return migration.get_instance_environment(instance=self.instance)


class WaitRsyncFromSnapshotDatabaseMigrate(RsyncFromSnapshotMigrateBackupHost):

    ATTEMPTS = 240
    DELAY = 30

    def __unicode__(self):
        return "Waiting rsync..."

    @property
    def is_valid(self):
        return self.is_database_instance

    def do(self):
        if not self.is_valid:
            return

        errors = 0
        script = 'ps -ef | grep sync | grep dbaas | wc -l'
        for attempt in range(self.ATTEMPTS):
            msg = 'Check rsync - attempt {} of {}'.format(
                attempt, self.ATTEMPTS
            )
            LOG.debug(msg)

            output = self.host.ssh.run_script(script, retry=True)
            exception_error = output.get('exception', '')
            if exception_error:
                if errors > 0:
                    raise Exception(exception_error)
                else:
                    msg = ('There was an exception when check rsync. '
                           'Exception: {}. '
                           'It will try to check rsync one more time.'
                           ''.format(exception_error))
                errors += 1
                sleep(self.DELAY)
                continue
            errors = 0

            rsync_process = int(output['stdout'][0])
            if rsync_process == 0:
                LOG.debug('RSYNC is not running')
                return

            LOG.debug('RSYNC is still running')
            sleep(self.DELAY)

        raise EnvironmentError(
            'RSYNC is still running.'
            'Wait rsync process finish before retry the task.'
        )

    def undo(self):
        pass


class AttachDataLatestVolumeMigrate(AttachDataVolume):
    @property
    def volume(self):
        return self.latest_disk

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def environment(self):
        return self.infra.environment

    @property
    def is_valid(self):
        if not super(AttachDataLatestVolumeMigrate, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.should_migrate_with_new_disk

    def undo(self):
        if self.is_valid and self.latest_disk and\
         not self.latest_disk.is_active:
            self.detach_disk(self.volume)


class DetachDataLatestVolumeMigrate(DetachDataVolume):
    @property
    def volume(self):
        return self.latest_disk

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def environment(self):
        return self.infra.environment

    @property
    def is_valid(self):
        if not super(DetachDataLatestVolumeMigrate, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.should_migrate_with_new_disk and\
            self.latest_disk and not self.latest_disk.is_active

    def do(self):
        if not self.is_valid:
            return

        self.detach_disk(self.volume)

    def undo(self):
        pass


class MountDataLatestVolumeMigrate(MountDataLatestVolume):
    @property
    def volume(self):
        return self.latest_disk

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def environment(self):
        return self.infra.environment

    @property
    def is_valid(self):
        if not super(MountDataLatestVolumeMigrate, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.should_migrate_with_new_disk

    def undo(self):
        pass


class UmountDataLatestVolumeMigrate(UnmountDataLatestVolume):
    @property
    def volume(self):
        return self.latest_disk

    @property
    def host(self):
        return self.host_migrate.host

    @property
    def environment(self):
        return self.infra.environment

    @property
    def is_valid(self):
        if not super(UmountDataLatestVolumeMigrate, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.should_migrate_with_new_disk and\
            self.latest_disk and not self.latest_disk.is_active

    def do(self):
        if self.is_valid:
            script = self.get_umount_command(
                self.latest_disk,
                data_directory=self.directory,
            )
            self.host.ssh.run_script(script)

    def undo(self):
        pass


class NewVolumeMigrateOriginalHost(NewVolumeMigrate):
    @property
    def is_valid(self):
        if not super(NewVolumeMigrateOriginalHost, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.should_migrate_with_new_disk

    @property
    def disk_offering_type(self):
        return self.infra.disk_offering_type.type

    def undo(self):
        if self.is_valid:
            if self.latest_disk and not self.latest_disk.is_active:
                self._remove_volume(self.latest_disk, self.host)


class DeleteVolumeMigrateOriginalHost(NewVolumeMigrateOriginalHost):

    def __unicode__(self):
        return "Delete volume migrate..."

    @property
    def is_valid(self):
        if not super(DeleteVolumeMigrateOriginalHost, self).is_valid\
         or not self.instance.is_database:
            return False
        return self.latest_disk and not self.latest_disk.is_active

    def do(self):
        if self.is_valid:
            self._remove_volume(self.latest_disk, self.host)

    def undo(self):
        pass
