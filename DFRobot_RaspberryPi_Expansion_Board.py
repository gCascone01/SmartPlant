# -*- coding:utf-8 -*-

import time
from smbus2 import SMBus

_ADC_CHAN_COUNT = 4

class DFRobot_Expansion_Board:
    _REG_SLAVE_ADDR = 0x00
    _REG_PID = 0x01
    _REG_VID = 0x02
    _REG_ADC_CTRL = 0x0e
    _REG_ADC_VAL1 = 0x0f
    _REG_ADC_VAL2 = 0x11
    _REG_ADC_VAL3 = 0x13
    _REG_ADC_VAL4 = 0x15

    _REG_DEF_PID = 0xdf
    _REG_DEF_VID = 0x10

    A0 = 0x00
    A1 = 0x01
    A2 = 0x02
    A3 = 0x03

    STA_OK = 0x00
    STA_ERR = 0x01
    STA_ERR_DEVICE_NOT_DETECTED = 0x02
    STA_ERR_SOFT_VERSION = 0x03
    STA_ERR_PARAMETER = 0x04

    last_operate_status = STA_OK
    ALL = 0xffffffff

    def __init__(self, addr):
        self._addr = addr

    def _write_bytes(self, reg, buf):
        pass

    def _read_bytes(self, reg, length):
        pass

    def begin(self):
        pid = self._read_bytes(self._REG_PID, 1)
        vid = self._read_bytes(self._REG_VID, 1)
        if self.last_operate_status == self.STA_OK:
            if pid[0] != self._REG_DEF_PID:
                self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
            elif vid[0] != self._REG_DEF_VID:
                self.last_operate_status = self.STA_ERR_SOFT_VERSION
            else:
                self.set_adc_disable()
        return self.last_operate_status

    def set_addr(self, addr):
        if addr < 1 or addr > 127:
            self.last_operate_status = self.STA_ERR_PARAMETER
            return
        self._write_bytes(self._REG_SLAVE_ADDR, [addr])

    def _parse_id(self, limit, id):
        if not isinstance(id, list):
            id = [id + 1]
        else:
            id = [i + 1 for i in id]
        if id == self.ALL:
            return range(1, limit + 1)
        for i in id:
            if i < 1 or i > limit:
                self.last_operate_status = self.STA_ERR_PARAMETER
                return []
        return id

    def set_adc_enable(self):
        self._write_bytes(self._REG_ADC_CTRL, [0x01])

    def set_adc_disable(self):
        self._write_bytes(self._REG_ADC_CTRL, [0x00])

    def get_adc_value(self, chan):
        for i in self._parse_id(_ADC_CHAN_COUNT, chan):
            rslt = self._read_bytes(self._REG_ADC_VAL1 + (i - 1) * 2, 2)
        return (rslt[0] << 8) | rslt[1]

    def detecte(self):
        l = []
        back = self._addr
        for i in range(1, 127):
            self._addr = i
            if self.begin() == self.STA_OK:
                l.append(hex(i))
        self._addr = back
        self.last_operate_status = self.STA_OK
        return l


class DFRobot_Expansion_Board_IIC(DFRobot_Expansion_Board):
    def __init__(self, bus_id, addr):
        self._bus = SMBus(bus_id)
        super().__init__(addr)

    def _write_bytes(self, reg, buf):
        self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
        try:
            self._bus.write_i2c_block_data(self._addr, reg, buf)
            self.last_operate_status = self.STA_OK
        except Exception:
            pass

    def _read_bytes(self, reg, length):
        self.last_operate_status = self.STA_ERR_DEVICE_NOT_DETECTED
        try:
            rslt = self._bus.read_i2c_block_data(self._addr, reg, length)
            self.last_operate_status = self.STA_OK
            return rslt
        except Exception:
            return [0] * length
