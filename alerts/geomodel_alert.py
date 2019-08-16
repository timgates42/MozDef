#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# Copyright (c) 2015 Mozilla Corporation

import json
import os
import sys
import traceback

from lib.alerttask import AlertTask
from mozdef_util.query_models import SearchQuery, QueryStringMatch as QSMatch
from mozdef_util.utilities.logger import logger

import alerts.geomodel.alert as alert
import alerts.geomodel.config as config
import alerts.geomodel.locality as locality


_CONFIG_FILE = os.path.join(
    os.path.dirname(__file__),
    'geomodel_alert.json')


class AlertGeoModel(AlertTask):
    '''GeoModel alert runs a set of configured queries for events and
    constructs locality state for users performing authenticated actions.
    When activity is found that indicates a potential compromise of an
    account, an alert is produced.
    '''

    def main(self):
        cfg = self._load_config()

        for query_index in range(len(cfg.events)):
            try:
                self._process(cfg, query_index)
            except Exception as err:
                traceback.print_exc(file=sys.stdout)
                logger.error(
                    'Error process events; query="{0}"; error={1}'.format(
                        cfg.events[query_index].lucene_query,
                        err.message))

    def onAggregation(self, agg):
        username = agg['value']
        events = agg['events']
        cfg = agg['config']

        localities = list(filter(map(locality.from_event, events)))
        new_state = locality.State('locality', username, localities)

        query = locality.wrap_query(self.es)
        journal = locality.wrap_journal(self.es)

        entry = locality.find(query, username, cfg.localities.es_index)
        if entry is None:
            entry = locality.Entry(
                '', locality.State('localities', username, []))

        updated = locality.Update.flat_map(
            lambda state: locality.remove_outdated(
                state,
                cfg.localities.valid_duration_days),
            locality.update(entry.state, new_state))

        if updated.did_update:
            entry.state = updated.state

            journal(entry, cfg.localities.es_index)

        new = alert.alert(entry.state, cfg.alerts.whitelist)

        if new is not None:
            # TODO: When we update to Python 3.7+, change to asdict(alert_produced)
            alert_dict = self.createAlertDict(
                new.summary,
                new.category,
                new.tags,
                events)

            alert_dict['details'] = {
                'username': new.username,
                'sourceipaddress': new.sourceipaddress,
                'origin': dict(new.origin._asdict())
            }

            return alert_dict

        return None

    def _process(self, cfg: config.Config, qindex: int):
        evt_cfg = cfg.events[qindex]

        search = SearchQuery(minutes=evt_cfg.search_window.minutes)
        search.add_must(QSMatch(evt_cfg.lucene_query))

        self.filtersManual(search)
        self.searchEventsAggregated(evt_cfg.username_path, samplesLimit=1000)
        self.walkAggregations(threshold=1, config=cfg)

    def _load_config(self):
        with open(_CONFIG_FILE) as cfg_file:
            cfg = json.load(cfg_file)

            cfg['localities'] = config.Localities(**cfg['localities'])

            for i, event in enumerate(cfg['events']):
                cfg['events'][i]['search_window'] = config.SearchWindow(
                    **cfg['events'][i]['search_window'])

            cfg['events'] = [config.Events(**dat) for dat in cfg['events']]

            cfg['alerts']['whitelist'] = config.Whitelist(
                **cfg['alerts']['whitelist'])
            cfg['alerts'] = config.Alerts(**cfg['alerts'])

            return config.Config(**cfg)
