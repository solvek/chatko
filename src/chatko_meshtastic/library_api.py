"""`LibraryMeshApi`: the `MeshApi` port over the official `meshtastic` library, TCP to the node.

The library is thread-based: each connection has a reader thread, a heartbeat timer, and blocking
calls to send. This adapter bridges it to asyncio: the reader thread hands every packet over with
`loop.call_soon_threadsafe`, and the blocking calls of a connection run one at a time in a thread
of its own (docs/architecture.md §3.6). It is the only module that imports the library.

The library would reconnect by itself when the node closes the connection, which races with the
reconnecting that `MeshNode` owns (spike S2, D25); `_Interface` turns that off, so a connection
simply ends. It also reads the node's settings and node database itself, as the node sends them
on connect: the library's node cache later takes keys from NodeInfo that the node rejected (D26).
"""

import asyncio
import contextlib
import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Final

from google.protobuf.message import DecodeError
from meshtastic.mesh_interface import MeshInterface
from meshtastic.protobuf import (
    admin_pb2,
    channel_pb2,
    config_pb2,
    mesh_pb2,
    module_config_pb2,
    portnums_pb2,
)
from meshtastic.tcp_interface import TCPInterface

from chatko_meshtastic.api import (
    AddContact,
    AdminCommand,
    BeginEdit,
    ChannelRole,
    ChannelSettings,
    CommitEdit,
    MeshApi,
    MeshConnection,
    MqttSettings,
    NodeEntry,
    NodeInfo,
    NodeSettings,
    NodeState,
    Other,
    Packet,
    Payload,
    RejectedError,
    Routing,
    SetChannel,
    SetLora,
    SetMqtt,
    SetOwner,
    SetPrivateKey,
    Text,
    UnreachableError,
    node_id,
)

CONNECT_TIMEOUT: Final = 15.0
"""Seconds to open the connection and to get the node's settings and node database."""

CHANNEL_SLOTS: Final = 8
MAX_PAYLOAD: Final = mesh_pb2.Constants.DATA_PAYLOAD_LEN
"""The most bytes one packet carries; a PKI direct message carries 12 bytes fewer (spike S2)."""

type SocketFactory = Callable[[], socket.socket]

_RegionCode = config_pb2.Config.LoRaConfig.RegionCode
_Role = channel_pb2.Channel.Role
_PortNum = portnums_pb2.PortNum


class _Interface(TCPInterface):
    """`TCPInterface` that never reconnects by itself and shows the adapter every message from
    the node, on the reader thread, before the library handles it.

    It overrides seven of the library's hooks (meshtastic 2.7): `myConnect` (the socket comes
    from the adapter); `_readBytes`, `_writeBytes` and `_reconnect` (a closed or broken connection
    ends instead of reconnecting); `_handleFromRadio` (the adapter's copy of each message);
    `_disconnected` (the end of the connection); and `_waitConnected`, so that waiting for the
    node's config ends when the connection does, and after the adapter's timeout.
    """

    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        open_socket: SocketFactory,
        on_message: Callable[[mesh_pb2.FromRadio], None],
        on_end: Callable[[], None],
        timeout: float,
    ) -> None:
        self._open_socket = open_socket
        self._on_message = on_message
        self._on_end = on_end
        self._config_timeout = timeout
        self.ended = threading.Event()
        super().__init__(
            hostname, portNumber=port, connectNow=False, timeout=max(1, round(timeout))
        )

    def myConnect(self) -> None:  # noqa: N802  # the library's name
        self.socket = self._open_socket()

    def _readBytes(self, length: int) -> bytes | None:  # noqa: N802
        try:
            return super()._readBytes(length)
        except OSError:
            self._reconnect()
            return None

    def _writeBytes(self, b: bytes) -> None:  # noqa: N802
        # The library's own logs an error that it reconnects; this one only ends the connection.
        if self.socket is not None:
            try:
                self.socket.sendall(b)
            except OSError:
                self._reconnect()
                raise

    def _reconnect(self) -> None:
        # The node closed the connection: it reboots, or another client connected. End it here;
        # MeshNode decides when to connect again.
        self._wantExit = True
        with contextlib.suppress(OSError):
            self._socket_shutdown()

    def _handleFromRadio(self, fromRadioBytes: bytes) -> None:  # noqa: N802, N803
        message = mesh_pb2.FromRadio()
        try:
            message.ParseFromString(fromRadioBytes)
        except DecodeError:
            logging.getLogger(__name__).warning("skipped a message the node sent that is not one")
            return
        if message.WhichOneof("payload_variant") == "rebooted":
            # The library would start over on the same connection; this one ends instead.
            self._reconnect()
            return
        self._on_message(message)
        super()._handleFromRadio(fromRadioBytes)  # type: ignore[no-untyped-call]

    def _disconnected(self) -> None:
        super()._disconnected()
        self.ended.set()
        self._on_end()

    def _waitConnected(self, timeout: float = 30.0) -> None:  # noqa: N802
        del timeout  # the adapter's own
        deadline = time.monotonic() + self._config_timeout
        while not self.isConnected.wait(0.05):
            if self.ended.is_set():
                raise ConnectionError("the node closed the connection")
            if time.monotonic() >= deadline:
                raise ConnectionError("the node did not send its settings")


def _name(name_of: Callable[[Any], str], value: int) -> str:
    """The name of a protobuf enum value, or the number for one newer than the library."""
    try:
        return name_of(value)
    except ValueError:
        return str(value)


class _Connection(MeshConnection):
    def __init__(self, loop: asyncio.AbstractEventLoop, address: str) -> None:
        self._loop = loop
        self._address = address
        self.executor = ThreadPoolExecutor(1, thread_name_prefix="chatko-meshtastic")
        self._queue: asyncio.Queue[Packet | None] = asyncio.Queue()
        self._iface: _Interface | None = None
        self._finished = False
        self._closed = False
        self._state: NodeState | None = None
        # What the node sent on connect, as it came (written by the reader thread until the node
        # says its config is complete, read only after that).
        self._num = 0
        self._nodes: dict[int, mesh_pb2.NodeInfo] = {}
        self._lora = config_pb2.Config.LoRaConfig()
        self._security = config_pb2.Config.SecurityConfig()
        self._mqtt = module_config_pb2.ModuleConfig.MQTTConfig()
        self._channels: dict[int, channel_pb2.Channel] = {}

    # Opening, in the connection's thread.

    def open(self, host: str, port: int, open_socket: SocketFactory, timeout: float) -> None:
        iface = _Interface(
            host,
            port,
            open_socket=open_socket,
            on_message=self._on_message,
            on_end=lambda: self._post(None),
            timeout=timeout,
        )
        self._iface = iface
        try:
            iface.connect()
        except BaseException:
            self.close_now()
            raise
        self._state = self._read_state()

    def close_now(self) -> None:
        iface = self._iface
        if iface is None:
            return
        iface.queueStatus = None  # a send waiting for room in the node's queue goes on and fails
        try:
            iface.close()
        except Exception:
            logging.getLogger(__name__).exception("could not close the connection to the node")

    def _on_message(self, message: mesh_pb2.FromRadio) -> None:
        match message.WhichOneof("payload_variant"):
            case "packet":
                self._post(_packet(message.packet))
            case "my_info":
                self._num = message.my_info.my_node_num
            case "node_info":
                info = mesh_pb2.NodeInfo()
                info.CopyFrom(message.node_info)
                self._nodes[info.num] = info
            case "config":
                if message.config.HasField("lora"):
                    self._lora.CopyFrom(message.config.lora)
                elif message.config.HasField("security"):
                    self._security.CopyFrom(message.config.security)
            case "moduleConfig":
                if message.moduleConfig.HasField("mqtt"):
                    self._mqtt.CopyFrom(message.moduleConfig.mqtt)
            case "channel":
                channel = channel_pb2.Channel()
                channel.CopyFrom(message.channel)
                self._channels[channel.index] = channel
            case _:
                pass

    def _post(self, item: Packet | None) -> None:
        with contextlib.suppress(RuntimeError):  # the event loop is closed
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _read_state(self) -> NodeState:
        own = self._nodes.get(self._num, mesh_pb2.NodeInfo())
        settings = NodeSettings(
            long_name=own.user.long_name,
            short_name=own.user.short_name,
            region=_name(_RegionCode.Name, self._lora.region),
            ok_to_mqtt=self._lora.config_ok_to_mqtt,
            ignore_mqtt=self._lora.ignore_mqtt,
            private_key=bytes(self._security.private_key),
            public_key=bytes(self._security.public_key),
            mqtt=MqttSettings(
                enabled=self._mqtt.enabled,
                address=self._mqtt.address,
                username=self._mqtt.username,
                password=self._mqtt.password,
                root=self._mqtt.root,
                encryption=self._mqtt.encryption_enabled,
                json=self._mqtt.json_enabled,
                tls=self._mqtt.tls_enabled,
                proxy_to_client=self._mqtt.proxy_to_client_enabled,
            ),
            channels=tuple(self._channel(index) for index in range(CHANNEL_SLOTS)),
        )
        nodes = tuple(
            NodeEntry(
                info.num,
                info.user.long_name,
                info.user.short_name,
                bytes(info.user.public_key),
                favorite=info.is_favorite,
            )
            for num, info in self._nodes.items()
            if num != self._num
        )
        return NodeState(self._num, settings, nodes)

    def _channel(self, index: int) -> ChannelSettings:
        channel = self._channels.get(index)
        if channel is None:
            return ChannelSettings(index)
        role = _name(_Role.Name, channel.role)
        return ChannelSettings(
            index,
            ChannelRole(role) if role in ChannelRole else ChannelRole.DISABLED,
            channel.settings.name,
            bytes(channel.settings.psk),
            channel.settings.uplink_enabled,
            channel.settings.downlink_enabled,
        )

    # The port.

    @property
    def state(self) -> NodeState:
        assert self._state is not None  # noqa: S101  # a connection is handed out once open
        return self._state

    async def events(self) -> AsyncIterator[Packet]:
        while not self._finished:
            item = await self._queue.get()
            if item is None:
                self._finished = True
                return
            yield item

    async def send_text(self, text: str, *, to: int, channel: int, want_ack: bool) -> int:
        data = text.encode()
        if len(data) > MAX_PAYLOAD:
            raise RejectedError(f"a text of {len(data)} bytes does not fit into a packet")

        def send(iface: _Interface) -> int:
            packet = iface.sendData(
                data,
                to,
                portNum=_PortNum.TEXT_MESSAGE_APP,
                wantAck=want_ack,
                channelIndex=channel,
            )
            return int(packet.id)

        return await self._call(send)

    async def send_admin(self, command: AdminCommand) -> int:
        message = self._admin_message(command)

        def send(iface: _Interface) -> int:
            packet = iface.sendData(
                message,
                self._num,
                portNum=_PortNum.ADMIN_APP,
                wantAck=True,
                wantResponse=True,
            )
            return int(packet.id)

        return await self._call(send)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self.close_now)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._queue.put_nowait(None)

    async def _call(self, function: Callable[[_Interface], int]) -> int:
        iface = self._iface
        if self._closed or iface is None or iface.ended.is_set():
            raise UnreachableError(f"the connection to the node at {self._address} ended")
        try:
            future: Future[int] = self.executor.submit(function, iface)
        except RuntimeError:  # closed meanwhile
            raise UnreachableError(f"the connection to the node at {self._address} ended") from None
        try:
            return await asyncio.wrap_future(future)
        except (OSError, MeshInterface.MeshInterfaceError) as error:
            raise UnreachableError(
                f"the node at {self._address} did not take it: {error}"
            ) from error

    def _admin_message(self, command: AdminCommand) -> admin_pb2.AdminMessage:
        admin = admin_pb2.AdminMessage()
        match command:
            case BeginEdit():
                admin.begin_edit_settings = True
            case CommitEdit():
                admin.commit_edit_settings = True
            case SetOwner():
                admin.set_owner.long_name = command.long_name
                admin.set_owner.short_name = command.short_name
            case SetLora():
                if command.region not in _RegionCode.keys():  # noqa: SIM118  # a list
                    raise RejectedError(f"this hub does not know the region {command.region}")
                lora = admin.set_config.lora
                lora.CopyFrom(self._lora)
                lora.region = _RegionCode.Value(command.region)
                lora.config_ok_to_mqtt = command.ok_to_mqtt
                lora.ignore_mqtt = command.ignore_mqtt
            case SetPrivateKey():
                security = admin.set_config.security
                security.CopyFrom(self._security)
                security.private_key = command.private_key
                security.public_key = b""  # the node derives it from the private key
            case SetMqtt():
                self._set_mqtt(admin.set_module_config.mqtt, command.mqtt)
            case SetChannel():
                self._set_channel(admin.set_channel, command.channel)
            case AddContact():
                contact = admin.add_contact
                contact.node_num = command.num
                known = self._nodes.get(command.num)
                if known is not None:
                    contact.user.hw_model = known.user.hw_model
                contact.user.id = node_id(command.num)
                contact.user.long_name = command.long_name
                contact.user.short_name = command.short_name
                contact.user.public_key = command.public_key
        return admin

    def _set_mqtt(
        self, mqtt: module_config_pb2.ModuleConfig.MQTTConfig, wanted: MqttSettings
    ) -> None:
        mqtt.CopyFrom(self._mqtt)
        mqtt.enabled = wanted.enabled
        mqtt.address = wanted.address
        mqtt.username = wanted.username
        mqtt.password = wanted.password
        mqtt.root = wanted.root
        mqtt.encryption_enabled = wanted.encryption
        mqtt.json_enabled = wanted.json
        mqtt.tls_enabled = wanted.tls
        mqtt.proxy_to_client_enabled = wanted.proxy_to_client

    def _set_channel(self, channel: channel_pb2.Channel, wanted: ChannelSettings) -> None:
        current = self._channels.get(wanted.index)
        if current is not None:
            channel.CopyFrom(current)
        channel.index = wanted.index
        channel.role = _Role.Value(wanted.role.value)
        channel.settings.name = wanted.name
        channel.settings.psk = wanted.psk
        channel.settings.uplink_enabled = wanted.uplink
        channel.settings.downlink_enabled = wanted.downlink


def _packet(packet: mesh_pb2.MeshPacket) -> Packet:
    payload: Payload = Other("")
    request_id = 0
    if packet.HasField("decoded"):
        data = packet.decoded
        request_id = data.request_id
        payload = _payload(data)
    return Packet(
        sender=int(getattr(packet, "from")),
        to=packet.to,
        packet_id=packet.id,
        payload=payload,
        channel=packet.channel,
        request_id=request_id,
        want_ack=packet.want_ack,
        pki=packet.pki_encrypted,
        public_key=bytes(packet.public_key),
        via_mqtt=packet.via_mqtt,
        hop_start=packet.hop_start,
        hop_limit=packet.hop_limit,
    )


def _payload(data: mesh_pb2.Data) -> Payload:
    port = _name(_PortNum.Name, data.portnum)
    try:
        match data.portnum:
            case _PortNum.TEXT_MESSAGE_APP:
                return Text(data.payload.decode("utf-8", errors="replace"), bool(data.emoji))
            case _PortNum.ROUTING_APP:
                routing = mesh_pb2.Routing()
                routing.ParseFromString(data.payload)
                return Routing(_name(mesh_pb2.Routing.Error.Name, routing.error_reason))
            case _PortNum.NODEINFO_APP:
                user = mesh_pb2.User()
                user.ParseFromString(data.payload)
                return NodeInfo(user.long_name, user.short_name, bytes(user.public_key))
            case _:
                return Other(port)
    except DecodeError:
        return Other(port)


class LibraryMeshApi(MeshApi):
    """Connects to a node's TCP API (a `meshtasticd` container) through the `meshtastic`
    library. `open_socket` replaces the TCP connection (tests)."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        open_socket: SocketFactory | None = None,
        timeout: float = CONNECT_TIMEOUT,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._open_socket = open_socket or self._tcp

    @property
    def address(self) -> str:
        return f"tcp {self._host}:{self._port}"

    def _tcp(self) -> socket.socket:
        sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        sock.settimeout(None)  # the reader thread blocks until the node sends something
        return sock

    async def connect(self) -> MeshConnection:
        connection = _Connection(asyncio.get_running_loop(), f"{self._host}:{self._port}")
        future = connection.executor.submit(
            connection.open, self._host, self._port, self._open_socket, self._timeout
        )
        try:
            await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            # The thread goes on connecting; close what it opens.
            future.add_done_callback(
                lambda _: threading.Thread(target=connection.close_now, daemon=True).start()
            )
            connection.executor.shutdown(wait=False)
            raise
        except Exception as error:
            connection.executor.shutdown(wait=False)
            raise UnreachableError(
                f"cannot connect to the node at {self.address}: {error}"
            ) from error
        return connection
