import { NextRequest, NextResponse } from 'next/server';
import { DynamoDBClient, GetItemCommand } from '@aws-sdk/client-dynamodb';

const dynamodbClient = new DynamoDBClient({
  region: process.env.DYNAMODB_AWS_REGION || 'ap-south-1',
  credentials: {
    accessKeyId: process.env.DYNAMODB_AWS_ACCESS_KEY_ID || '',
    secretAccessKey: process.env.DYNAMODB_AWS_SECRET_ACCESS_KEY || '',
  },
});

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const conversationId = searchParams.get('conversationId');

  if (!conversationId) {
    return NextResponse.json({ error: 'conversationId is required' }, { status: 400 });
  }

  try {
    const command = new GetItemCommand({
      TableName: process.env.DYNAMODB_TABLE_NAME || 'voice_bot_tc',
      Key: {
        conversationId: { S: conversationId },
      },
    });

    const response = await dynamodbClient.send(command);

    if (!response.Item) {
      return NextResponse.json({ error: 'Conversation not found' }, { status: 404 });
    }

    // Return the specific conversation data parsed if available
    if (response.Item.conversation_data?.S) {
      return NextResponse.json(JSON.parse(response.Item.conversation_data.S));
    }

    return NextResponse.json({ error: 'conversation_data empty' }, { status: 404 });
  } catch (error) {
    console.error('DynamoDB GetItem Error:', error);
    return NextResponse.json({ error: 'Internal Server Error' }, { status: 500 });
  }
}
