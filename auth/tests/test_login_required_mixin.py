from django.http import HttpResponse, HttpResponseRedirect
from django.test import TestCase
from django.test.client import RequestFactory
from django.core.urlresolvers import reverse
from django.views.generic.base import View

from auth.views import LoginRequiredMixin

from mock import patch, Mock


class LoginRequiredMixinTest(TestCase):

    @patch("requests.get")
    def test_should_redirect_to_login_page_if_user_is_not_authenticated(self, get):
        get.return_value = Mock(status_code=200)
        request = RequestFactory().get('/')
        request.session = {}
        response = StubView.as_view()(request)
        self.assertIsInstance(response, HttpResponseRedirect)
        expected_url = "%s?next=%s" % (reverse('login'), request.path)
        self.assertEqual(expected_url, response['Location'])

    @patch("requests.get")
    def test_should_invoke_view_dispatch_when_user_is_authenticated(self, get):
        get.return_value = Mock(status_code=200)
        request = RequestFactory().get('/')
        request.session = {'tsuru_token': 'my beautiful token'}
        response = StubView.as_view()(request)
        self.assertEqual('ok', response.content)


class StubView(LoginRequiredMixin, View):

    def get(self, request):
        return HttpResponse('ok')
