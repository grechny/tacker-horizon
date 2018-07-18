# Copyright 2015 Brocade Communications System, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import codecs
import os

from django.forms import ValidationError
from django.utils.translation import ugettext_lazy as _

from horizon import exceptions
from horizon import forms
from horizon import messages

from tacker_horizon.openstack_dashboard import api
from toscaparser.tosca_template import ToscaTemplate

DESCRIPTORS_PATH = "/var/lib/tacker/"

class OnBoardVNF(forms.SelfHandlingForm):
    name = forms.CharField(max_length=255, label=_("Name"))
    description = forms.CharField(widget=forms.widgets.Textarea(
                                  attrs={'rows': 4}),
                                  label=_("Description"),
                                  required=False)
    source_type = forms.ChoiceField(
        label=_('TOSCA Template Source'),
        required=False,
        choices=[('file', _('TOSCA Template File')),
                 ('raw', _('Direct Input'))],
        widget=forms.Select(
            attrs={'class': 'switchable', 'data-slug': 'source'}))

    toscal_file = forms.FileField(
        label=_("TOSCA Template File"),
        help_text=_("A local TOSCA template file to upload."),
        widget=forms.FileInput(
            attrs={'class': 'switched', 'data-switch-on': 'source',
                   'data-source-file': _('TOSCA Template File')}),
        required=False)

    direct_input = forms.CharField(
        label=_('TOSCA YAML'),
        help_text=_('The YAML formatted contents of a TOSCA template.'),
        widget=forms.widgets.Textarea(
            attrs={'class': 'switched', 'data-switch-on': 'source',
                   'data-source-raw': _('TOSCA YAML')}),
        required=False)

    def __init__(self, request, *args, **kwargs):
        super(OnBoardVNF, self).__init__(request, *args, **kwargs)

    def clean(self):
        data = super(OnBoardVNF, self).clean()

        # The key can be missing based on particular upload
        # conditions. Code defensively for it here...
        toscal_file = data.get('toscal_file', None)
        toscal_raw = data.get('direct_input', None)
        source_type = data.get("source_type")
        if source_type == "file" and not toscal_file:
            raise ValidationError(
                _("No TOSCA template file selected."))
        if source_type == "raw" and not toscal_raw:
            raise ValidationError(
                _("No direct input specified."))

        if toscal_file and not toscal_file.name.endswith(('.yaml', '.csar')):
            raise ValidationError(_("Only .yaml or .csar file uploads \
                                    are supported"))

        try:
            if toscal_file:
                toscal_str = self.files['toscal_file'].read()
                if toscal_file.name.endswith('.csar'):
                    data['archive'] = toscal_str
                else:
                    data['tosca'] = toscal_str
            else:
                data['tosca'] = data['direct_input']
        except Exception as e:
            msg = _('There was a problem loading the namespace: %s.') % e
            raise forms.ValidationError(msg)

        return data

    def handle(self, request, data):
        try:
            toscal = data.get('tosca')
            archive = data.get('archive')
            vnfd_name = data['name']
            vnfd_description = data['description']
            if toscal:
                tosca_arg = {'vnfd': {'name': vnfd_name,
                                      'description': vnfd_description,
                                      'attributes': {'vnfd': toscal}}}
            else:
                tosca_arg = {'vnfd': {'name': vnfd_name,
                                      'description': vnfd_description}}
            vnfd_instance = api.tacker.create_vnfd(request, tosca_arg)
            if archive:
                vnfd_id = vnfd_instance["vnfd"]['id'].encode("utf-8")
                upload_folder = DESCRIPTORS_PATH + vnfd_id
                os.makedirs(upload_folder)
                # save temporary file to uploaded dir
                filename = os.path.join(DESCRIPTORS_PATH, 'tmp_' + vnfd_id + '.csar')
                with open(filename, 'wb') as file:
                    file.write(archive)
                # extract and validate CSAR with TOSCA parser
                tosca = ToscaTemplate(filename, None, True, None, None, upload_folder)
                # get main template for VNFD attribute
                f = codecs.open(tosca.path, encoding='utf-8', errors='strict')
                main_template = f.read()
                f.close()
                toscal = main_template.encode("utf-8")

                # remove temporary archive
                os.remove(filename)

                # update descriptor with VNFD from main template
                tosca_arg = {"vnfd": {"attributes": {"vnfd": toscal}}}
                api.tacker.upload_vnfd(request, vnfd_id, tosca_arg)

            messages.success(request,
                             _('VNF Catalog entry %s has been created.') %
                             vnfd_instance['vnfd']['name'])
            return toscal
        except Exception as e:
            msg = _('Unable to create TOSCA. %s')
            msg %= e.message.split('Failed validating', 1)[0]
            exceptions.handle(request, message=msg)
            return False
