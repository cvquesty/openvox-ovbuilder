import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Alert, Button, Stack, Text } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('OV Builder UI crash', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <Stack p="xl" maw={560} mx="auto" mt="xl">
          <Alert color="red" icon={<IconAlertCircle size={16} />} title="Something broke">
            <Text size="sm" mb="md">{this.state.error.message}</Text>
            <Button color="red" onClick={() => this.setState({ error: null })}>
              Try again
            </Button>
          </Alert>
        </Stack>
      );
    }
    return this.props.children;
  }
}
