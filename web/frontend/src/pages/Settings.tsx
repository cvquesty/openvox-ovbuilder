import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Center,
  Divider,
  Group,
  Loader,
  NumberInput,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconAlertCircle, IconCheck, IconSettings } from '@tabler/icons-react';
import { settings, type RuntimeSettingsUpdate, type WebhookFormat } from '../services/api';
import { UserRolesCard } from './UserRoles';

type FormValues = {
  vsphere_server: string;
  vsphere_user: string;
  vsphere_password: string;
  vsphere_datacenter: string;
  vsphere_ignore_ssl: boolean;
  npm_registry: string;
  notify_webhook_url: string;
  notify_on_success: boolean;
  notify_on_failure: boolean;
  notify_on_cancelled: boolean;
  notify_webhook_format: WebhookFormat;
  public_base_url: string;
  notify_text_template: string;
  notify_email_subject_template: string;
  notify_email_body_template: string;
  notify_email_enabled: boolean;
  notify_email_owner: boolean;
  notify_email_to: string;
  smtp_host: string;
  smtp_port: number | string;
  smtp_user: string;
  smtp_password: string;
  smtp_starttls: boolean;
  smtp_ssl: boolean;
  smtp_from: string;
};

const NOTIFY_DEFAULTS = {
  notify_webhook_format: 'slack' as WebhookFormat,
  public_base_url: '',
  notify_text_template: '',
  notify_email_subject_template: '',
  notify_email_body_template: '',
  notify_email_enabled: false,
  notify_email_owner: true,
  notify_email_to: '',
  smtp_host: '',
  smtp_port: 587 as number | string,
  smtp_user: '',
  smtp_password: '',
  smtp_starttls: true,
  smtp_ssl: false,
  smtp_from: '',
};

const TEMPLATE_HELP =
  'Leave blank for the built-in default. Variables: $status $hostname $requested_by $job_id $ip ' +
  '$vm_ip $os_image $environment $error $duration $started_at $finished_at $job_url.';

export function SettingsPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [passwordSet, setPasswordSet] = useState(false);
  const [webhookSet, setWebhookSet] = useState(false);
  const [smtpPasswordSet, setSmtpPasswordSet] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);

  const form = useForm<FormValues>({
    initialValues: {
      vsphere_server: '',
      vsphere_user: '',
      vsphere_password: '',
      vsphere_datacenter: '',
      vsphere_ignore_ssl: true,
      npm_registry: 'https://registry.npmjs.org/',
      notify_webhook_url: '',
      notify_on_success: true,
      notify_on_failure: true,
      notify_on_cancelled: true,
      ...NOTIFY_DEFAULTS,
    },
    validate: {
      public_base_url: (v) => {
        const t = (v || '').trim();
        if (!t) return null;
        try {
          const u = new URL(t);
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'Use http:// or https://';
        } catch {
          return 'Enter a valid URL';
        }
        return null;
      },
      smtp_host: (v, values) =>
        values.notify_email_enabled && !(v || '').trim() ? 'SMTP host is required for email' : null,
      smtp_port: (v) => {
        const n = Number(v);
        return Number.isInteger(n) && n >= 1 && n <= 65535 ? null : 'Port 1–65535';
      },
      vsphere_server: (v) => ((v || '').trim() ? null : 'vSphere server is required'),
      npm_registry: (v) => {
        const t = (v || '').trim();
        if (!t) return 'npm registry URL is required';
        try {
          const u = new URL(t);
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'Use http:// or https://';
        } catch {
          return 'Enter a valid URL';
        }
        return null;
      },
      notify_webhook_url: (v) => {
        const t = (v || '').trim();
        if (!t) return null;
        try {
          const u = new URL(t);
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'Use http:// or https://';
        } catch {
          return 'Enter a valid webhook URL';
        }
        return null;
      },
    },
  });

  useEffect(() => {
    settings
      .get()
      .then((data) => {
        setPasswordSet(data.vsphere_password_set);
        setWebhookSet(data.notify_webhook_set);
        setSmtpPasswordSet(data.smtp_password_set);
        form.setValues({
          vsphere_server: data.vsphere_server || '',
          vsphere_user: data.vsphere_user || '',
          vsphere_password: '',
          vsphere_datacenter: data.vsphere_datacenter || '',
          vsphere_ignore_ssl: data.vsphere_ignore_ssl,
          npm_registry: data.npm_registry || 'https://registry.npmjs.org/',
          notify_webhook_url: '',
          notify_on_success: data.notify_on_success,
          notify_on_failure: data.notify_on_failure,
          notify_on_cancelled: data.notify_on_cancelled,
          notify_webhook_format: data.notify_webhook_format || 'slack',
          public_base_url: data.public_base_url || '',
          notify_text_template: data.notify_text_template || '',
          notify_email_subject_template: data.notify_email_subject_template || '',
          notify_email_body_template: data.notify_email_body_template || '',
          notify_email_enabled: data.notify_email_enabled,
          notify_email_owner: data.notify_email_owner,
          notify_email_to: data.notify_email_to || '',
          smtp_host: data.smtp_host || '',
          smtp_port: data.smtp_port || 587,
          smtp_user: data.smtp_user || '',
          smtp_password: '',
          smtp_starttls: data.smtp_starttls,
          smtp_ssl: data.smtp_ssl,
          smtp_from: data.smtp_from || '',
        });
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load settings'))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  const onSubmit = form.onSubmit(async (values) => {
    setSaving(true);
    setError(null);
    const body: RuntimeSettingsUpdate = {
      vsphere_server: values.vsphere_server.trim(),
      vsphere_user: values.vsphere_user.trim(),
      vsphere_datacenter: values.vsphere_datacenter.trim(),
      vsphere_ignore_ssl: values.vsphere_ignore_ssl,
      npm_registry: values.npm_registry.trim(),
      notify_on_success: values.notify_on_success,
      notify_on_failure: values.notify_on_failure,
      notify_on_cancelled: values.notify_on_cancelled,
      notify_webhook_format: values.notify_webhook_format,
      public_base_url: values.public_base_url.trim(),
      notify_text_template: values.notify_text_template,
      notify_email_subject_template: values.notify_email_subject_template,
      notify_email_body_template: values.notify_email_body_template,
      notify_email_enabled: values.notify_email_enabled,
      notify_email_owner: values.notify_email_owner,
      notify_email_to: values.notify_email_to.trim(),
      smtp_host: values.smtp_host.trim(),
      smtp_port: Number(values.smtp_port),
      smtp_user: values.smtp_user.trim(),
      smtp_starttls: values.smtp_starttls,
      smtp_ssl: values.smtp_ssl,
      smtp_from: values.smtp_from.trim(),
    };
    if (values.vsphere_password !== '') body.vsphere_password = values.vsphere_password;
    if (values.notify_webhook_url !== '') body.notify_webhook_url = values.notify_webhook_url.trim();
    if (values.smtp_password !== '') body.smtp_password = values.smtp_password;
    try {
      const saved = await settings.update(body);
      setPasswordSet(saved.vsphere_password_set);
      setWebhookSet(saved.notify_webhook_set);
      setSmtpPasswordSet(saved.smtp_password_set);
      form.setFieldValue('vsphere_password', '');
      form.setFieldValue('notify_webhook_url', '');
      form.setFieldValue('smtp_password', '');
      notifications.show({
        title: 'Settings saved',
        message: 'Configuration updated for OV Builder.',
        color: 'teal',
        icon: <IconCheck size={16} />,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  });

  if (loading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  return (
    <Stack gap="lg" maw={720}>
      <div>
        <Group gap="sm" mb={4}>
          <IconSettings size={22} stroke={1.6} />
          <Title order={2}>Configuration</Title>
        </Group>
        <Text c="dimmed" size="sm">
          Admin-only settings for vSphere, package registries, notifications, and local role
          overrides. Secrets are stored on the server and never returned to the browser.
        </Text>
      </div>

      {error && (
        <Alert
          ref={errorRef}
          tabIndex={-1}
          color="red"
          icon={<IconAlertCircle size={16} />}
          title="Could not save"
          withCloseButton
          onClose={() => setError(null)}
        >
          {error}
        </Alert>
      )}

      <form onSubmit={onSubmit}>
        <Stack gap="md">
          <Card withBorder padding="lg" radius="md">
            <Title order={4} mb="xs">
              vSphere / VMware
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Used by inventory and VM lifecycle actions. Leave the password blank to keep the
              current value.
            </Text>
            <Stack gap="sm">
              <TextInput
                label="Server hostname"
                placeholder="vcenter.example.com"
                autoComplete="off"
                {...form.getInputProps('vsphere_server')}
              />
              <TextInput
                label="Username"
                placeholder="rachel.c@example.org"
                autoComplete="off"
                {...form.getInputProps('vsphere_user')}
              />
              <PasswordInput
                label="Password"
                placeholder={passwordSet ? '••••••••  (leave blank to keep)' : 'Enter password'}
                description={passwordSet ? 'A password is already stored on the server.' : undefined}
                autoComplete="new-password"
                {...form.getInputProps('vsphere_password')}
              />
              <TextInput
                label="Datacenter"
                placeholder="optional"
                autoComplete="off"
                {...form.getInputProps('vsphere_datacenter')}
              />
              <Switch
                label="Ignore SSL certificate errors"
                description="Lab default. Turn off in production when using trusted certs."
                {...form.getInputProps('vsphere_ignore_ssl', { type: 'checkbox' })}
              />
            </Stack>
          </Card>

          <Card withBorder padding="lg" radius="md">
            <Title order={4} mb="xs">
              Package resources
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Prefer the public npm registry unless you intentionally use a private mirror
              reachable from this host and CI.
            </Text>
            <TextInput
              label="npm registry URL"
              placeholder="https://registry.npmjs.org/"
              autoComplete="off"
              {...form.getInputProps('npm_registry')}
            />
          </Card>

          <Card withBorder padding="lg" radius="md">
            <Title order={4} mb="xs">
              Notifications
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Optional webhook and email when a build finishes. Every channel is off until
              configured. The webhook URL and SMTP password are stored on the server and never
              shown again after save. Delivery failures are logged and never fail a build.
            </Text>
            <Stack gap="sm">
              <Text fw={600} size="sm">
                Events (all channels)
              </Text>
              <Switch
                label="Notify on success"
                {...form.getInputProps('notify_on_success', { type: 'checkbox' })}
              />
              <Switch
                label="Notify on failure"
                {...form.getInputProps('notify_on_failure', { type: 'checkbox' })}
              />
              <Switch
                label="Notify on cancel"
                {...form.getInputProps('notify_on_cancelled', { type: 'checkbox' })}
              />
              <TextInput
                label="Public URL of this app"
                description="Used for $job_url links, e.g. https://builder.example.com"
                placeholder="https://builder.example.com"
                autoComplete="off"
                {...form.getInputProps('public_base_url')}
              />

              <Divider my="xs" />
              <Text fw={600} size="sm">
                Webhook (Slack or generic JSON)
              </Text>
              <PasswordInput
                label="Webhook URL"
                placeholder={
                  webhookSet ? '••••••••  (leave blank to keep)' : 'https://example.com/webhook'
                }
                description={
                  webhookSet
                    ? 'A webhook is already stored. Paste a new URL to replace it, or clear via API with an empty string.'
                    : 'Slack incoming webhook or any endpoint that accepts {"text": "…"} JSON.'
                }
                autoComplete="off"
                {...form.getInputProps('notify_webhook_url')}
              />
              <Select
                label="Payload format"
                data={[
                  { value: 'slack', label: 'Slack incoming webhook ({"text": …})' },
                  { value: 'json', label: 'Generic JSON (event + job fields + text)' },
                ]}
                allowDeselect={false}
                {...form.getInputProps('notify_webhook_format')}
              />
              <Textarea
                label="Message template"
                description={TEMPLATE_HELP}
                placeholder="OV Builder $status: $hostname by $requested_by$details$job_link"
                autosize
                minRows={1}
                {...form.getInputProps('notify_text_template')}
              />

              <Divider my="xs" />
              <Text fw={600} size="sm">
                Email (SMTP)
              </Text>
              <Switch
                label="Send email notifications"
                {...form.getInputProps('notify_email_enabled', { type: 'checkbox' })}
              />
              <Switch
                label="Email the user who requested the build"
                description="Uses the directory email from login. Users can opt out on My notifications."
                {...form.getInputProps('notify_email_owner', { type: 'checkbox' })}
              />
              <TextInput
                label="Additional recipients"
                description="Comma-separated, e.g. an ops list. Receives every enabled event."
                placeholder="ops@example.com"
                autoComplete="off"
                {...form.getInputProps('notify_email_to')}
              />
              <Group grow align="flex-start">
                <TextInput
                  label="SMTP host"
                  placeholder="smtp.example.com"
                  autoComplete="off"
                  {...form.getInputProps('smtp_host')}
                />
                <NumberInput
                  label="Port"
                  min={1}
                  max={65535}
                  allowDecimal={false}
                  {...form.getInputProps('smtp_port')}
                />
              </Group>
              <Group grow align="flex-start">
                <TextInput
                  label="SMTP username"
                  placeholder="optional"
                  autoComplete="off"
                  {...form.getInputProps('smtp_user')}
                />
                <PasswordInput
                  label="SMTP password"
                  placeholder={smtpPasswordSet ? '••••••••  (leave blank to keep)' : 'optional'}
                  description={smtpPasswordSet ? 'A password is already stored.' : undefined}
                  autoComplete="new-password"
                  {...form.getInputProps('smtp_password')}
                />
              </Group>
              <TextInput
                label="From address"
                placeholder="ovbuilder@example.com (defaults to SMTP username)"
                autoComplete="off"
                {...form.getInputProps('smtp_from')}
              />
              <Group>
                <Switch
                  label="STARTTLS"
                  {...form.getInputProps('smtp_starttls', { type: 'checkbox' })}
                />
                <Switch
                  label="Implicit TLS (SMTPS, usually port 465)"
                  {...form.getInputProps('smtp_ssl', { type: 'checkbox' })}
                />
              </Group>
              <TextInput
                label="Subject template"
                description={TEMPLATE_HELP}
                placeholder="[OV Builder] $hostname $status"
                autoComplete="off"
                {...form.getInputProps('notify_email_subject_template')}
              />
              <Textarea
                label="Body template"
                placeholder="Leave blank for the built-in plain-text summary"
                autosize
                minRows={3}
                {...form.getInputProps('notify_email_body_template')}
              />
            </Stack>
          </Card>

          <Divider />
          <Group>
            <Button type="submit" loading={saving}>
              Save configuration
            </Button>
          </Group>
        </Stack>
      </form>

      <UserRolesCard />
    </Stack>
  );
}
