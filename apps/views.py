import json
import requests

from django.template.response import TemplateResponse
from django.conf import settings
from django.http import HttpResponse, HttpResponseServerError, Http404
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.views.generic import TemplateView
from django.contrib import messages

from pygments import highlight
from pygments.lexers import DiffLexer
from pygments.formatters import HtmlFormatter

from apps.forms import AppForm, AppAddTeamForm, RunForm, SetEnvForm
from auth.views import LoginRequiredView, LoginRequiredMixin


class DeployInfo(LoginRequiredMixin, TemplateView):
    template_name = "apps/deploy.html"

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get_context_data(self, *args, **kwargs):
        deploy_id = kwargs["deploy"]
        context = super(DeployInfo, self).get_context_data(*args, **kwargs)

        url = "{}/deploys/{}".format(settings.TSURU_HOST, deploy_id)
        response = requests.get(url, headers=self.authorization)
        context["deploy"] = response.json()

        diff = context["deploy"].get("Diff")
        if diff and diff != u'The deployment must have at least two commits for the diff.':
            format = HtmlFormatter()
            diff = highlight(diff, DiffLexer(), format)
        else:
            diff = None

        context["deploy"]["Diff"] = diff

        class App(object):
            def __init__(self, name):
                self.name = name

        app_name = kwargs["app_name"]
        context['app'] = App(name=app_name)
        return context


class ListDeploy(LoginRequiredView):
    template = "apps/deploys.html"

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get(self, request, *args, **kwargs):
        app_name = kwargs["app_name"]

        page = int(request.GET.get('page', '1'))

        skip = (page * 20) - 20
        limit = page * 20

        url = '{}/deploys?app={}&skip={}&limit={}'.format(settings.TSURU_HOST, app_name,
                                                          skip, limit)
        response = requests.get(url, headers=self.authorization)

        deploys = []
        if response.status_code != 204:
            deploys = response.json() or []

        context = {}
        context['deploys'] = deploys

        class App(object):
            def __init__(self, name):
                self.name = name

        context['app'] = App(name=app_name)

        if len(deploys) >= 20:
            context['next'] = page + 1

        if page > 0:
            context['previous'] = page - 1

        return TemplateResponse(request, self.template, context=context)


class ChangeUnit(LoginRequiredView):
    def add_unit(self, units, app_name):
        requests.put(
            "{0}/apps/{1}/units".format(settings.TSURU_HOST, app_name),
            headers=self.authorization,
            data=str(units)
        )

    def remove_units(self, units, app_name):
        requests.delete(
            "{0}/apps/{1}/units".format(settings.TSURU_HOST, app_name),
            headers=self.authorization,
            data=str(units)
        )

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get_app(self, app_name):
        url = '{0}/apps/{1}'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def post(self, request, *args, **kwargs):
        app_name = kwargs['app_name']

        app = self.get_app(app_name)

        app_units = len(app["units"])
        units = int(request.POST["units"])

        if len(app["units"]) < int(request.POST['units']):
            self.add_unit(units - app_units, app_name)

        if len(app["units"]) > int(request.POST['units']):
            self.remove_units(app_units - units, app_name)

        return redirect(reverse('detail-app', args=[app_name]))


class AppDetail(LoginRequiredMixin, TemplateView):
    template_name = 'apps/details.html'

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def service_instances(self, app_name):
        tsuru_url = '{}/services/instances?app={}'.format(settings.TSURU_HOST, app_name)
        return requests.get(tsuru_url, headers=self.authorization).json()

    def get_containers(self, app_name):
        if not self.request.session.get("is_admin"):
            return []

        url = "{}/docker/node/apps/{}/containers".format(settings.TSURU_HOST, app_name)
        response = requests.get(url, headers=self.authorization)

        if response.status_code == 204:
            return []

        data = response.json()
        if not data:
            return []

        return data

    def get_context_data(self, *args, **kwargs):
        context = super(AppDetail, self).get_context_data(*args, **kwargs)
        app_name = kwargs["app_name"]
        token = self.request.session.get('tsuru_token')
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        headers = {
            'content-type': 'application/json',
            'Authorization': token,
        }

        response = requests.get(url, headers=headers)
        if response.status_code == 404:
            raise Http404()

        context['app'] = response.json()

        service_instances = []

        for service in self.service_instances(app_name):
            if service["instances"]:
                service_instances.append(
                    {"name": service["instances"][0], "servicename": service["service"]}
                )

        context['app']["service_instances"] = service_instances

        for container in self.get_containers(app_name):
            for index, unit in enumerate(context['app']['units']):
                if unit["Name"] == container["ID"]:
                    context['app']['units'][index].update({
                        'HostAddr': container['HostAddr'],
                        'HostPort': container['HostPort'],
                    })
        return context


class CreateApp(LoginRequiredView):
    template_name = 'apps/create.html'

    def render(self, request, context):
        return TemplateResponse(request, self.template_name, context)

    def get(self, request):
        form = AppForm()
        default, plans = self.plans(request)
        form.fields['plan'].choices = plans
        form.fields['plan'].initial = default
        form.fields['platform'].choices = self.platforms(request)
        form.fields['teamOwner'].choices = self.teams(request)
        form.fields['pool'].choices = self.pools(request)
        context = {
            "app_form": form,
        }
        return self.render(request, context)

    def post(self, request):
        context = {}
        form = AppForm(request.POST)
        default, plans = self.plans(request)
        form.fields['plan'].choices = plans
        form.fields['platform'].choices = self.platforms(request)
        form.fields['teamOwner'].choices = self.teams(request)
        form.fields['pool'].choices = self.pools(request)
        if form.is_valid():
            authorization = {'authorization': request.session.get('tsuru_token')}

            data = form.data.dict()
            if data.get("plan"):
                data["plan"] = {"name": data["plan"]}

            data = json.dumps(data)

            url = '{}/apps'.format(settings.TSURU_HOST)
            response = requests.post(url, data=data, headers=authorization)

            if response.status_code == 200:
                messages.success(request, u"App was successfully created", fail_silently=True)
                return redirect(reverse('list-app'))

            context['errors'] = response.content

        form.fields['plan'].initial = default
        context['app_form'] = form
        return self.render(request, context)

    def pools(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        url = "{}/pools".format(settings.TSURU_HOST)
        response = requests.get(url, headers=authorization)
        pools = set()
        for team_list in response.json():
            for pool in team_list["Pools"]:
                pools.add(pool)
        result = [("", "")]
        result.extend([(p, p) for p in pools])
        return result

    def teams(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        url = "{}/teams".format(settings.TSURU_HOST)
        response = requests.get(url, headers=authorization)
        teams = response.json()
        result = [("", "")]
        result.extend([(t["name"], t["name"]) for t in teams])
        return result

    def platforms(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        response = requests.get("%s/platforms" % settings.TSURU_HOST, headers=authorization)
        platforms = response.json()
        result = [("", "")]
        result.extend([(p["Name"], p["Name"]) for p in platforms])
        return result

    def plans(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        url = "{}/plans".format(settings.TSURU_HOST)
        response = requests.get(url, headers=authorization)
        plans = response.json()
        plan_list = [("", "")]
        default = ""
        for p in plans:
            if p.get("default"):
                default = p["name"]
            plan_list.append((p["name"], p["name"]))
        return default, plan_list


class AppAddTeam(LoginRequiredView):
    template = "apps/app_add_team.html"

    def get(self, request, app_name):
        context = {}
        context['app_name'] = app_name
        context['app'] = {"name": app_name}
        context['form'] = AppAddTeamForm()
        context['teams'] = self._get_teams(request)
        return TemplateResponse(request, self.template, context=context)

    def post(self, request, app_name):
        form = AppAddTeamForm(request.POST)
        if not form.is_valid():
            return TemplateResponse(request, self.template, {'form': form})

        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = '%s/apps/%s/%s' % (settings.TSURU_HOST, app_name,
                                       form.data.get('team'))
        response = requests.put(tsuru_url, headers=authorization)
        if response.status_code == 200:
            return TemplateResponse(
                request, self.template,
                {'form': form, 'app_name': app_name,
                 'message': "The Team was successfully added"}
            )
        return TemplateResponse(request, self.template,
                                {'form': form, 'app_name': app_name,
                                 'errors': response.content})

    def _get_teams(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        response = requests.get("%s/teams" % settings.TSURU_HOST,
                                headers=authorization)
        teams = response.json()
        return [t["name"] for t in teams]


class AppRevokeTeam(LoginRequiredView):
    def get(self, request, app_name, team):
        app_name = self.kwargs['app_name']

        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = "{0}/apps/{1}/{2}".format(
            settings.TSURU_HOST, app_name, team)
        requests.delete(tsuru_url, headers=authorization)

        return redirect(reverse('app-teams', args=[app_name]))


class RemoveApp(LoginRequiredView):
    def get(self, request, *args, **kwargs):
        app_name = self.kwargs["name"]
        authorization = {'authorization': request.session.get('tsuru_token')}
        response = requests.delete(
            "{0}/apps/{1}".format(settings.TSURU_HOST, app_name),
            headers=authorization
        )
        if response.status_code > 399:
            return HttpResponse(response.text, status=response.status_code)
        return redirect(reverse('list-app'))


class ListApp(LoginRequiredView):
    def get(self, request):
        authorization = {'authorization': request.session.get('tsuru_token')}
        response = requests.get('%s/apps' % settings.TSURU_HOST,
                                headers=authorization)
        if response.status_code == 204:
            apps = []
        else:
            apps = sorted(response.json(), key=lambda item: item["name"])
        return TemplateResponse(request, "apps/list.html", {'apps': apps})


class Run(LoginRequiredView):
    template = "apps/run.html"

    def get(self, request):
        context = {}
        context['form'] = RunForm()
        context['apps'] = self._get_apps(request)
        return TemplateResponse(request, self.template, context=context)

    def post(self, request):
        form = RunForm(request.POST)
        if not form.is_valid():
            return TemplateResponse(request, self.template, {'form': form})

        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = '%s/apps/%s/run' % (settings.TSURU_HOST,
                                        form.data.get('app'))
        response = requests.post(tsuru_url, data=form.data.get('command'),
                                 headers=authorization)
        if response.status_code == 200:
            return TemplateResponse(request, self.template,
                                    {'form': form,
                                     'message': response.content})
        return TemplateResponse(request, self.template,
                                {'form': form, 'errors': response.content})

    def _get_apps(self, request):
        authorization = {"authorization": request.session.get("tsuru_token")}
        response = requests.get("%s/apps" % settings.TSURU_HOST,
                                headers=authorization)
        apps = response.json()
        return [a["name"] for a in apps]


class AppLog(LoginRequiredView):
    template = "apps/app_log.html"

    def get(self, request, app_name):
        authorization = {"authorization": request.session.get("tsuru_token")}
        app_url = "{}/apps/{}".format(settings.TSURU_HOST, app_name)
        app = requests.get(app_url, headers=authorization).json()
        log_url = "{}/apps/{}/log?lines=100".format(settings.TSURU_HOST, app_name)
        logs = requests.get(log_url, headers=authorization).json()
        return TemplateResponse(request, self.template, {'logs': logs, 'app': app})


class AppTeams(LoginRequiredView):
    template = "apps/app_team.html"

    def get(self, request, app_name):
        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = '%s/apps/%s' % (settings.TSURU_HOST, app_name)
        response = requests.get(tsuru_url, headers=authorization)

        if response.status_code == 200:
            app = response.json
            return TemplateResponse(request, self.template, {'app': app})
        return TemplateResponse(request, self.template,
                                {'errors': response.content})


class AppEnv(LoginRequiredView):
    template = "apps/app_env.html"

    def get(self, request, app_name):
        context = {}
        context['app'] = app_name
        context['form'] = SetEnvForm(initial=context)

        response = self.get_envs(request, app_name)

        if response.status_code == 200:
            envs = response.content.split('\n')
            context['envs'] = envs
            return TemplateResponse(request, self.template, context)
        return TemplateResponse(request, self.template,
                                {'errors': response.content})

    def post(self, request, app_name):
        context = {}
        context['app'] = app_name

        response = self.get_envs(request, app_name)
        if response.status_code == 200:
            form = SetEnvForm(request.POST)
            context['form'] = form
            if not form.is_valid():
                return TemplateResponse(request, self.template, context)

            envs = response.content.split('\n')
            envs.append(request.POST['env'])
            context['envs'] = envs

            response = self.set_env(request, app_name, form)

            if response.status_code == 200:
                context['message'] = response.content
                return TemplateResponse(request, self.template, context)

        context['errors'] = response.content
        return TemplateResponse(request, self.template, context)

    def get_envs(self, request, app_name):
        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = '%s/apps/%s/env' % (settings.TSURU_HOST, app_name)
        return requests.get(tsuru_url, headers=authorization)

    def set_env(self, request, app_name, form):
        authorization = {'authorization': request.session.get('tsuru_token')}
        tsuru_url = '%s/apps/%s/env' % (settings.TSURU_HOST, app_name)
        return requests.post(tsuru_url, data=form.data.get('env'),
                             headers=authorization)


class MetricDetail(LoginRequiredMixin, TemplateView):
    template_name = 'apps/metric_details.html'

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get_envs(self, app_name):
        url = "{}/apps/{}/env".format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_app(self, app_name):
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_context_data(self, *args, **kwargs):
        context = super(MetricDetail, self).get_context_data(*args, **kwargs)
        app_name = kwargs["app_name"]

        context['app'] = self.get_app(app_name)
        context['app']['envs'] = self.get_envs(app_name)

        return context


class AppRollback(LoginRequiredView):
    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get(self, request, app_name, image):
        url = '{}/apps/{}/deploy/rollback'.format(settings.TSURU_HOST, app_name)
        response = requests.post(url, headers=self.authorization, data={'image': image})
        if response.status_code == 200:
            return redirect(reverse('app-deploys', args=[app_name]))
        return HttpResponseServerError("NOT OK")


class Settings(LoginRequiredMixin, TemplateView):
    template_name = 'apps/settings.html'

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get_envs(self, app_name):
        url = "{}/apps/{}/env".format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_context_data(self, *args, **kwargs):
        context = super(Settings, self).get_context_data(*args, **kwargs)
        app_name = kwargs["app_name"]
        token = self.request.session.get('tsuru_token')
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        headers = {
            'content-type': 'application/json',
            'Authorization': token,
        }

        context['app'] = requests.get(url, headers=headers).json()
        context['app']['envs'] = self.get_envs(app_name)

        return context


class Metrics(LoginRequiredMixin, TemplateView):
    template_name = 'apps/metrics.html'

    @property
    def authorization(self):
        return {'authorization': self.request.session.get('tsuru_token')}

    def get_envs(self, app_name):
        url = "{}/apps/{}/env".format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_app(self, app_name):
        url = '{}/apps/{}'.format(settings.TSURU_HOST, app_name)
        return requests.get(url, headers=self.authorization).json()

    def get_context_data(self, *args, **kwargs):
        context = super(Metrics, self).get_context_data(*args, **kwargs)
        app_name = kwargs["app_name"]

        context['app'] = self.get_app(app_name)
        context['app']['envs'] = self.get_envs(app_name)

        return context
