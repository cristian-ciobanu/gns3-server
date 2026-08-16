#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2021 GNS3 Technologies Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


import logging
from .controller_error import ControllerError

log = logging.getLogger(__name__)


class ApplianceToTemplate:
    """
    Appliance installation.
    """

    def new_template(self, appliance_config, version, server):
        """
        Creates a new template from an appliance.
        """

        if appliance_config.get("registry_version", 0) >= 8:
            return self._new_template_v8(appliance_config, version, server)

        new_template = {
            "compute_id": server,
            "name": appliance_config["name"],
        }

        if version:
            new_template["version"] = version.get("name")

        if "usage" in appliance_config:
            new_template["usage"] = appliance_config["usage"]

        if appliance_config["category"] == "multilayer_switch":
            new_template["category"] = "switch"
        else:
            new_template["category"] = appliance_config["category"]

        if "symbol" in appliance_config:
            new_template["symbol"] = appliance_config.get("symbol")

        if "tags" in appliance_config:
            new_template["tags"] = appliance_config.get("tags")

        if new_template.get("symbol") is None:
            if appliance_config["category"] == "guest":
                if "docker" in appliance_config:
                    new_template["symbol"] = ":/symbols/docker_guest.svg"
                else:
                    new_template["symbol"] = ":/symbols/qemu_guest.svg"
            elif appliance_config["category"] == "router":
                new_template["symbol"] = ":/symbols/router.svg"
            elif appliance_config["category"] == "switch":
                new_template["symbol"] = ":/symbols/ethernet_switch.svg"
            elif appliance_config["category"] == "multilayer_switch":
                new_template["symbol"] = ":/symbols/multilayer_switch.svg"
            elif appliance_config["category"] == "firewall":
                new_template["symbol"] = ":/symbols/firewall.svg"

        if "qemu" in appliance_config:
            new_template["template_type"] = "qemu"
            self._add_qemu_config(new_template, appliance_config, version)
        elif "iou" in appliance_config:
            new_template["template_type"] = "iou"
            self._add_iou_config(new_template, appliance_config, version)
        elif "dynamips" in appliance_config:
            new_template["template_type"] = "dynamips"
            self._add_dynamips_config(new_template, appliance_config, version)
        elif "docker" in appliance_config:
            new_template["template_type"] = "docker"
            self._add_docker_config(new_template, appliance_config)

        return new_template

    def _add_qemu_config(self, new_config, appliance_config, version):

        new_config.update(appliance_config["qemu"])

        # the following properties are not valid for a template
        new_config.pop("kvm", None)
        new_config.pop("path", None)
        new_config.pop("arch", None)

        options = appliance_config["qemu"].get("options", "")
        if appliance_config["qemu"].get("kvm", "allow") == "disable" and "-machine accel=tcg" not in options:
            options += " -machine accel=tcg"
        new_config["options"] = options.strip()
        new_config.update(version.get("images"))

        if "arch" in appliance_config["qemu"]:
            new_config["platform"] = appliance_config["qemu"]["arch"]

        if "first_port_name" in appliance_config:
            new_config["first_port_name"] = appliance_config["first_port_name"]

        if "port_name_format" in appliance_config:
            new_config["port_name_format"] = appliance_config["port_name_format"]

        if "port_segment_size" in appliance_config:
            new_config["port_segment_size"] = appliance_config["port_segment_size"]

        if "custom_adapters" in appliance_config:
            new_config["custom_adapters"] = appliance_config["custom_adapters"]

        if "linked_clone" in appliance_config:
            new_config["linked_clone"] = appliance_config["linked_clone"]

    def _add_docker_config(self, new_config, appliance_config):

        new_config.update(appliance_config["docker"])

        if "custom_adapters" in appliance_config:
            new_config["custom_adapters"] = appliance_config["custom_adapters"]

    def _add_dynamips_config(self, new_config, appliance_config, version):

        new_config.update(appliance_config["dynamips"])
        new_config["idlepc"] = version.get("idlepc", "")
        new_config["image"] = version.get("images").get("image")

    def _add_iou_config(self, new_config, appliance_config, version):

        new_config.update(appliance_config["iou"])
        new_config["path"] = version.get("images").get("image")

    def _new_template_v8(self, appliance_config, version, server):
        """
        Creates a new template from an appliance using the registry version 8 format.
        """

        settings = self._select_v8_settings(appliance_config, version)
        properties = self._merge_v8_properties(settings, appliance_config)

        new_template = {
            "compute_id": server,
            "template_type": settings["template_type"],
            "name": appliance_config["name"],
        }

        if version:
            new_template["version"] = version.get("name")

        # category/usage/symbol can be defined in template_properties (already merged above),
        # otherwise at the version level, otherwise at the appliance level
        for prop in ("category", "usage", "symbol"):
            if prop not in properties:
                if version and version.get(prop) is not None:
                    properties[prop] = version[prop]
                elif appliance_config.get(prop) is not None:
                    properties[prop] = appliance_config[prop]

        if properties.get("category") == "multilayer_switch":
            properties["category"] = "switch"

        new_template.update(properties)
        if "tags" in appliance_config:
            new_template["tags"] = appliance_config.get("tags")

        if not new_template.get("symbol"):
            # apply a default symbol based on the category and template type
            if appliance_config["category"] == "guest":
                if settings["template_type"] == "docker":
                    new_template["symbol"] = ":/symbols/docker_guest.svg"
                else:
                    new_template["symbol"] = ":/symbols/qemu_guest.svg"
            else:
                symbols = {
                    "router": ":/symbols/router.svg",
                    "switch": ":/symbols/ethernet_switch.svg",
                    "multilayer_switch": ":/symbols/multilayer_switch.svg",
                    "firewall": ":/symbols/firewall.svg",
                }
                new_template["symbol"] = symbols.get(appliance_config["category"])

        if version and version.get("images"):
            new_template.update(version["images"])

        return new_template

    def _select_v8_settings(self, appliance_config, version):
        """
        Selects the settings set to use: the one referenced by the version,
        otherwise the default set, otherwise the only set present.
        """

        settings_list = appliance_config.get("settings") or []
        if not settings_list:
            raise ControllerError(f"Appliance '{appliance_config['name']}' has no settings")

        if version and version.get("settings"):
            settings_name = version["settings"]
            for settings in settings_list:
                if settings.get("name") == settings_name:
                    return settings
            raise ControllerError(
                f"Could not find settings '{settings_name}' referenced by "
                f"version '{version.get('name')}' in appliance '{appliance_config['name']}'"
            )

        for settings in settings_list:
            if settings.get("default"):
                return settings

        if len(settings_list) == 1:
            return settings_list[0]

        raise ControllerError(
            f"Appliance '{appliance_config['name']}' has multiple settings "
            f"but none is marked as default"
        )

    def _merge_v8_properties(self, settings, appliance_config):
        """
        Merges the template properties of the selected settings with the default
        settings properties, unless inheritance is disabled or the default set
        is selected.
        """

        properties = {}
        if not settings.get("default") and settings.get("inherit_default_properties", True):
            for other_settings in appliance_config.get("settings") or []:
                if other_settings.get("default"):
                    properties.update(other_settings.get("template_properties") or {})
        properties.update(settings.get("template_properties") or {})
        return properties
